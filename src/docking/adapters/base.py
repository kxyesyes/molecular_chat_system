"""Small command adapter primitives for docking command line tools."""

import os
import signal
import subprocess
from typing import List, Optional, Union


class CommandAdapter:
    """Wraps a command line executable with consistent path and run helpers."""

    def __init__(self, executable: Optional[str] = ""):
        self.executable = os.path.abspath(executable) if executable else ""

    @property
    def exists(self) -> bool:
        return bool(self.executable and os.path.exists(self.executable))

    def wrap_command(self, *args: str) -> List[str]:
        if self.executable.lower().endswith(".bat"):
            return ["cmd", "/c", self.executable, *args]
        return [self.executable, *args]

    def run(
        self,
        args: List[str],
        cwd: Optional[str] = None,
        timeout: Optional[Union[int, float]] = None,
    ):
        popen_kwargs = {}
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True

        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
            **popen_kwargs,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._terminate_process_tree(process)
            stdout, stderr = process.communicate()
            raise subprocess.TimeoutExpired(
                cmd="[redacted command]",
                timeout=timeout,
                output=stdout,
                stderr=stderr,
            ) from None

        return subprocess.CompletedProcess(
            args=args,
            returncode=process.returncode,
            stdout=stdout,
            stderr=stderr,
        )

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen) -> None:
        if os.name == "nt":
            taskkill = None
            try:
                taskkill = subprocess.Popen(
                    [
                        "taskkill",
                        "/PID",
                        str(process.pid),
                        "/T",
                        "/F",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                taskkill.communicate(timeout=10)
                if taskkill.returncode != 0 and process.poll() is None:
                    process.kill()
            except subprocess.TimeoutExpired:
                taskkill.kill()
                taskkill.communicate()
                if process.poll() is None:
                    process.kill()
            except OSError:
                if process.poll() is None:
                    process.kill()
            return

        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        except OSError:
            if process.poll() is None:
                process.kill()
