"""Meeko or legacy ligand preparation adapter."""

import os

from .base import CommandAdapter


class MeekoAdapter(CommandAdapter):
    """Runs ligand preparation through Meeko or a compatible script."""

    def build_prepare_command(self, input_path: str, output_path: str):
        command_name = os.path.basename(self.executable).lower()
        if command_name.startswith("mk_prepare_ligand"):
            return self.wrap_command("-i", input_path, "-o", output_path)
        return self.wrap_command("-l", input_path, "-o", output_path)

    def prepare_ligand(
        self,
        input_path: str,
        output_path: str,
        cwd: str,
        timeout: int = 60,
        *,
        cancel_event=None,
    ):
        return self.run(
            self.build_prepare_command(input_path, output_path),
            cwd=cwd,
            timeout=timeout,
            cancel_event=cancel_event,
        )
