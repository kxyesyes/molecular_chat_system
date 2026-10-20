"""One application-owned, non-daemon full-corpus acquisition worker.

Status is an observation, never a load/retry authority or scientific receipt.
Closing revokes readiness immediately and retains the worker until actual exit.
"""
import asyncio
from contextlib import contextmanager
import logging
import threading
import time

from src.system.rdkit_logging import initialize_rdkit_logging

from .compact_source import CompactSourcePolicy, acquisition_check
from .owned_source import ReverseSourceUnavailable, clear_failure_frames
from .predictor import ReverseTargetPredictor


logger = logging.getLogger(__name__)


class SourceRuntime:
    def __init__(self, data_dir, *, policy=None, timeout_seconds=1800):
        self._policy = CompactSourcePolicy() if policy is None else policy
        if type(self._policy) is not CompactSourcePolicy:
            raise ValueError("Invalid reverse-target prewarm policy.")
        self._policy.validate()
        acquisition_check(timeout_seconds, None)
        # Bootstrap before dispatch. Concurrent embedders must call this during
        # their own single-threaded startup; never change sinks inside _run().
        initialize_rdkit_logging()
        self._timeout = timeout_seconds
        self._producer = ReverseTargetPredictor(data_dir)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._foreground_io_count = 0
        self._start_done = threading.Event()
        self._start_done.set()
        self._thread = None
        self._state, self._phase, self._reason = "idle", "idle", None
        self._verified = self._total = 0
        self._began = self._ended = None

    def start(self):
        with self._lock:
            if self._state != "idle":
                return
            self._state, self._phase = "loading", "validating_source"
            self._began = time.monotonic()
            self._start_done.clear()
        try:
            worker = threading.Thread(target=self._run, name="reverse-source-prewarm", daemon=False)
            with self._lock:
                self._thread = worker
            worker.start()
        except BaseException as error:
            self._stop.set()
            self._producer.close_strict()
            with self._lock:
                if self._state in {"loading", "ready"}:
                    self._state, self._phase = "failed", "failed"
                    self._reason = "prewarm_start_failed"
                    self._ended = time.monotonic()
            clear_failure_frames(error)
            raise
        finally:
            # A start() that raises after dispatch still leaves its worker owned.
            self._start_done.set()

    def _progress(self, phase, verified, total):
        if (phase != "validating_rows" or type(verified) is not int or type(total) is not int
                or not 0 <= verified <= total <= self._policy.max_rows):
            raise ValueError("Invalid reverse-target prewarm progress.")
        with self._lock:
            if verified < self._verified or (self._total and self._total != total):
                raise ValueError("Invalid reverse-target prewarm progress.")
            if self._state == "loading":
                self._phase, self._verified, self._total = phase, verified, total

    def _run(self):
        reason = None

        def cancelled():
            if self._stop.is_set():
                return True
            with self._lock:
                foreground_pending = self._foreground_io_count > 0
            if foreground_pending:
                # Briefly release the GIL only while actual config I/O needs it.
                # Existing acquisition checks still account for this wait in
                # their deadline; closing interrupts the wait immediately.
                self._stop.wait(0.001)
            return self._stop.is_set()

        try:
            self._producer.initialize_compact_strict(policy=self._policy,
                timeout_seconds=self._timeout, cancelled=cancelled, progress=self._progress)
        except TimeoutError as error:
            reason = "acquisition_timeout"
            clear_failure_frames(error)
        except asyncio.CancelledError as error:
            reason = "acquisition_cancelled"
            clear_failure_frames(error)
        except BaseException as error:
            reason = "source_validation_failed"
            clear_failure_frames(error)
        finally:
            with self._lock:
                publish = reason is None and self._state == "loading" and not self._stop.is_set()
                if publish:
                    self._state, self._phase = "ready", "ready"
                elif self._state == "loading":
                    self._state, self._phase = "failed", "failed"
                    self._reason = reason or "source_unavailable"
                self._ended = time.monotonic()
            if not publish:
                self._producer.close_strict()
            status = self.snapshot()
            logger.info("Reverse-target source prewarm %s (%d/%d rows)",
                        status["state"], status["verified_rows"], status["total_rows"])

    @contextmanager
    def foreground_io(self):
        """Give concurrent config I/O workers room until physical I/O returns."""
        with self._lock:
            active = not self._stop.is_set()
            if active:
                self._foreground_io_count += 1
        try:
            yield
        finally:
            if active:
                with self._lock:
                    self._foreground_io_count -= 1

    def snapshot(self):
        with self._lock:
            pending = (not self._start_done.is_set()
                       or (self._thread is not None and (
                           self._thread in threading.enumerate() or self._thread.is_alive())))
            elapsed = 0.0 if self._began is None else max(
                0.0, (self._ended if self._ended is not None else time.monotonic()) - self._began)
            return dict(state=self._state, phase=self._phase, verified_rows=self._verified,
                        total_rows=self._total, elapsed_seconds=round(elapsed, 3),
                        reason=self._reason, cleanup_pending=pending)

    def ready_predictor(self):
        with self._lock:
            if self._state != "ready" or self._stop.is_set():
                raise ReverseSourceUnavailable()
            return self._producer

    def request_close(self):
        with self._lock:
            if self._state == "closed":
                return
            self._stop.set()
            self._state, self._phase = "closing", "closing"
        # Never invoke producer code under the runtime lock.
        self._producer.close_strict()

    def close(self):
        """Drain on a non-event-loop thread; no deadline claims a false close."""
        self.request_close()
        self._start_done.wait()
        worker = self._thread
        if worker is threading.current_thread():
            raise RuntimeError("A prewarm worker cannot join itself.")
        if worker is not None:
            # enumerate() includes a dispatched bootstrap before Thread sets ident.
            # An interrupted start handshake is not proof that dispatch failed.
            # A normal native allocation failure removes the unstarted thread;
            # uncertain dispatch remains owned without a fabricated deadline.
            while worker in threading.enumerate() and not worker.is_alive():
                time.sleep(0.01)  # Bootstrap has not reached the join handshake.
            if worker.ident is not None:
                worker.join()  # Also drain the short post-enumeration exit window.
        with self._lock:
            self._state, self._phase = "closed", "closed"
            if self._ended is None:
                self._ended = time.monotonic()
