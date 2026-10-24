"""Bounded JSONL transport for one isolated official Pi process per run.

The bridge owns process/I/O lifetime, not model iteration or scientific tasks.
Callbacks retain their existing cancellation and physical-settlement ownership.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4


MAX_FRAME_BYTES = 1024 * 1024
MAX_FRAME_DEPTH = 32  # Pi envelopes add levels around depth-16 decision DTOs.
HOST_PATH = Path(__file__).resolve().parents[3] / 'runtime/pi/host.mjs'


class PiBridgeError(RuntimeError):
    """Fixed, payload-free bridge diagnostic."""


def child_environment():
    # No credentials, proxy variables, NODE_OPTIONS, extension or Pi config paths.
    allowed = {'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'TEMP', 'TMP', 'TMPDIR',
               'LANG', 'LC_ALL'}
    result = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    result.update(PI_OFFLINE='1', NO_COLOR='1')
    return result


def decode_frame(raw):
    if len(raw) > MAX_FRAME_BYTES:
        raise PiBridgeError('pi_frame_too_large')
    if not raw.endswith(b'\n'):
        raise PiBridgeError('pi_protocol_invalid')
    try:
        from src.agent.contracts.decision import decode_protocol_json
        frame = decode_protocol_json(raw.decode('utf-8'), max_bytes=MAX_FRAME_BYTES,
                                     max_depth=MAX_FRAME_DEPTH)
    except (ValueError, UnicodeError):
        raise PiBridgeError('pi_protocol_invalid') from None
    if (type(frame) is not dict or type(frame.get('v')) is not int or frame['v'] != 1
            or frame.get('type') not in {'ready', 'request', 'event', 'result', 'failed'}):
        raise PiBridgeError('pi_protocol_invalid')
    return frame


def _write(process, frame):
    raw = (json.dumps({'v': 1, **frame}, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
    if len(raw) > MAX_FRAME_BYTES:
        raise PiBridgeError('pi_frame_too_large')
    process.stdin.write(raw)
    process.stdin.flush()


def _close(process):
    # Killing the protocol child does NOT cancel scientific workers. Callers must
    # settle their callback owners before publishing a terminal AgentResult.
    if process.poll() is None:
        process.kill()
    process.wait(timeout=10)
    process.stdin.close()
    process.stdout.close()


async def _drain_cleanup(task):
    interrupted = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            interrupted = True
    task.result()
    if interrupted:
        raise asyncio.CancelledError


class PiRuntimeBridge:
    def __init__(self, *, node=None, host_path=HOST_PATH):
        self.node = node or shutil.which('node')
        self.host_path = Path(host_path)
        self.active_pid = None

    async def run(self, options, *, request_model, execute_tool, on_event=None):
        if self.active_pid is not None:
            raise PiBridgeError('pi_bridge_already_running')
        if not self.node or not self.host_path.is_file():
            raise PiBridgeError('pi_runtime_unavailable')
        # Popen with threaded pipe I/O also works on Windows selector event loops.
        # No shell, detached helper, inherited stdin, or credential environment.
        try:
            process = subprocess.Popen(
                [self.node, str(self.host_path)], cwd=str(self.host_path.parent),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env=child_environment(), bufsize=0,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
        except OSError:
            raise PiBridgeError('pi_runtime_unavailable') from None
        self.active_pid = process.pid
        run_id = uuid4().hex
        seen = set()
        try:
            async def read():
                raw = await asyncio.to_thread(process.stdout.readline, MAX_FRAME_BYTES + 1)
                if not raw:
                    raise PiBridgeError('pi_process_exited')
                return decode_frame(raw)
            ready = await asyncio.wait_for(read(), timeout=30)
            if ready != {'v': 1, 'type': 'ready'}:
                raise PiBridgeError('pi_protocol_invalid')
            await asyncio.to_thread(_write, process, {'type': 'start', 'runId': run_id, 'options': options})
            # This is an I/O pump. Pi alone decides whether/when to request a model
            # transaction or execute a tool. No next-action policy exists here.
            while True:
                frame = await asyncio.wait_for(read(), timeout=60)
                if frame.get('runId') != run_id:
                    raise PiBridgeError('pi_identity_mismatch')
                kind = frame['type']
                if kind == 'event':
                    if on_event is not None:
                        on_event(frame['event'])
                elif kind == 'request':
                    request_id = frame.get('id')
                    if (type(request_id) is not str or request_id != str(len(seen) + 1)
                            or request_id in seen or len(seen) >= 64):
                        raise PiBridgeError('pi_request_identity_invalid')
                    seen.add(request_id)
                    callback = {'model': request_model, 'tool': execute_tool}.get(frame.get('operation'))
                    if callback is None:
                        raise PiBridgeError('pi_operation_forbidden')
                    payload = await callback(frame['payload'])
                    await asyncio.to_thread(_write, process,
                        {'type': 'response', 'runId': run_id, 'id': request_id, 'payload': payload})
                elif kind == 'result':
                    result = frame.get('result')
                    if (type(result) is not dict or result.get('runtime') != 'pi'
                            or result.get('terminated') is not True):
                        raise PiBridgeError('pi_terminal_missing')
                    return result
                elif kind == 'failed':
                    code = frame.get('code')
                    raise PiBridgeError(code if code in {
                        'pi_model_budget', 'pi_terminal_missing', 'pi_cancelled',
                    } else 'pi_runtime_failed')
                else:
                    raise PiBridgeError('pi_protocol_invalid')
        finally:
            try:
                await _drain_cleanup(asyncio.create_task(asyncio.to_thread(_close, process)))
            finally:
                self.active_pid = None
