"""ADFRsuite receptor preparation adapter."""

from .base import CommandAdapter


class ADFRAdapter(CommandAdapter):
    """Runs ADFRsuite prepare_receptor commands."""

    def build_prepare_command(self, receptor_path: str, output_path: str):
        return self.wrap_command("-r", receptor_path, "-o", output_path)

    def prepare_receptor(self, receptor_path: str, output_path: str, cwd: str):
        return self.run(self.build_prepare_command(receptor_path, output_path), cwd=cwd)
