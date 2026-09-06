"""AutoDock Vina adapter."""

from typing import Optional, Union

from .base import CommandAdapter


class VinaAdapter(CommandAdapter):
    """Runs AutoDock Vina with a generated config file."""

    def build_run_command(self, config_path: str):
        return self.wrap_command("--config", config_path)

    def run_config(
        self,
        config_path: str,
        cwd: str,
        timeout: Optional[Union[int, float]] = None,
        *,
        cancel_event=None,
    ):
        return self.run(
            self.build_run_command(config_path),
            cwd=cwd,
            timeout=timeout,
            cancel_event=cancel_event,
        )
