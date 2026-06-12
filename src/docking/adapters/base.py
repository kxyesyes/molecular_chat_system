"""Small command adapter primitives for docking command line tools."""

import os
import subprocess
from typing import List, Optional


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

    def run(self, args: List[str], cwd: Optional[str] = None, timeout: Optional[int] = None):
        return subprocess.run(args, capture_output=True, text=True, cwd=cwd, timeout=timeout)
