"""Thread-scoped privacy for synchronous RDKit diagnostics.

BlockLogs restores process-wide enable flags and cannot protect overlapping
workers. LogToPythonLogger ALSO resets those flags: its installation is a
single-threaded bootstrap operation, never a concurrent-request operation.

Applications/embedders must initialize before starting Python OR native workers
and must not subsequently switch RDKit to another sink. The Python thread check
rejects known-unsafe late initialization; it cannot discover foreign native work.
After bootstrap, guards only filter their calling thread's synchronous logs.
"""
from contextlib import contextmanager
import logging
import threading


_lock = threading.Lock()
_local = threading.local()
_installed = False


class _ThreadPrivacyFilter(logging.Filter):
    def filter(self, record):
        return not getattr(_local, "depth", 0)


_filter = _ThreadPrivacyFilter()


def initialize_rdkit_logging():
    """Install once, before concurrency; preserve configured native/Python logs.

    Standalone single-threaded callers may initialize on first use. A late caller
    must fail closed rather than briefly enabling logs belonging to BlockLogs in
    another request. Save/restore is safe ONLY under this bootstrap contract.
    """
    global _installed
    with _lock:
        if _installed:
            return
        main = threading.main_thread()
        if threading.current_thread() is not main or threading.enumerate() != [main]:
            raise RuntimeError(
                "Initialize RDKit privacy logging on the main thread before starting worker threads."
            )
        logger = logging.getLogger("rdkit")
        # RDKit's FIRST import adds a handler and sets WARNING/propagate=False.
        # Preserve an explicitly configured logger before that import, not just
        # before LogToPythonLogger(). A bare getLogger() lookup is not config:
        # leave RDKit's intended defaults intact in that case.
        configured = (logger.handlers or logger.filters or logger.level != logging.NOTSET
                      or not logger.propagate or logger.disabled)
        before = (list(logger.handlers), logger.level, logger.propagate, logger.disabled) if configured else None
        try:
            from rdkit import rdBase
        finally:
            if before is not None:
                handlers, level, propagate, disabled = before
                logger.handlers[:] = handlers
                logger.setLevel(level)
                logger.propagate, logger.disabled = propagate, disabled

        flags = [line.split(":") for line in rdBase.LogStatus().splitlines()]
        already_filtered = _filter in logger.filters
        logger.addFilter(_filter)
        try:
            try:
                rdBase.LogToPythonLogger()
            finally:
                # Even a router that fails after changing the native sink may
                # have reset flags. No private work is entered on failure.
                for name, state in flags:
                    (rdBase.EnableLog if state == "enabled" else rdBase.DisableLog)(name)
        except BaseException:
            if not already_filtered:
                logger.removeFilter(_filter)
            raise
        _installed = True


@contextmanager
def suppress_rdkit_diagnostics():
    """Suppress synchronous diagnostics; never bootstrap from a worker thread."""
    initialize_rdkit_logging()
    depth = getattr(_local, "depth", 0)
    _local.depth = depth + 1
    try:
        yield
    finally:
        _local.depth = depth
