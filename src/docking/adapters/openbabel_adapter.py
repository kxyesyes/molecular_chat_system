"""OpenBabel adapter placeholder for fallback conversion workflows."""

from .base import CommandAdapter


class OpenBabelAdapter(CommandAdapter):
    """Wraps OpenBabel when a fallback conversion path is needed."""

    def convert(
        self,
        input_path: str,
        output_path: str,
        cwd: str,
        *,
        cancel_event=None,
    ):
        cmd = self.wrap_command(input_path, "-O", output_path)
        return self.run(cmd, cwd=cwd, cancel_event=cancel_event)
