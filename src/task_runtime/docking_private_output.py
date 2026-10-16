"""Constructor-only C output binding; no public policy/path configuration."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from .config import PROJECT_ROOT
from .docking_consent import DockingConsentError, DockingConsentPolicy
from .docking_execution import DockingExecution
from .staging import _reject_link_components


def private_output_root(value, *, staging_root, excluded=()):
    """Reject overlapping trees and existing links/reparse points without creating I/O."""
    try:
        root = Path(value)
        if not root.is_absolute() or ".." in root.parts:
            raise ValueError
        _reject_link_components(root)
        for component in (root, *root.parents):
            if component.exists() and not component.is_dir():
                raise ValueError
        resolved = root.resolve()
        if resolved != root:
            raise ValueError
        forbidden = (
            PROJECT_ROOT / "temp_docking", Path.cwd() / "temp_docking",
            PROJECT_ROOT / "src/web/static", Path.cwd() / "src/web/static",
            Path(staging_root), *excluded,
        )
        for path in forbidden:
            other = Path(path).resolve()
            if root == other or root in other.parents or other in root.parents:
                raise ValueError
        return root
    except (OSError, ValueError, TypeError, RuntimeError):
        raise DockingConsentError("consent_policy_unavailable") from None


@dataclass(frozen=True, repr=False)
class PrivateDockingOutput:
    execution: DockingExecution
    root: Path
    staging_root: Path
    policy: DockingConsentPolicy
    excluded: tuple[Path, ...]
    injected: bool
    raw_executor: object
    stager: object
    completions: object
    chat_toolchain: object = None

    @classmethod
    def bind(cls, execution, policy, staging_root, *, excluded=()):
        if type(execution) is not DockingExecution or type(policy) is not DockingConsentPolicy:
            raise DockingConsentError("consent_policy_unavailable")
        root = private_output_root(execution._allowed_output_root,
            staging_root=staging_root, excluded=excluded)
        binding = cls(execution, root, Path(staging_root).resolve(), policy,
                      tuple(excluded), execution._raw_executor_injected, execution.raw_executor,
                      execution._stager, execution._completions, execution._chat_toolchain)
        binding.require(execution, policy)
        return binding

    def require(self, execution, policy):
        if (execution is not self.execution or policy is not self.policy
                or execution._allowed_output_root != self.root
                or execution._raw_executor_injected is not self.injected
                or execution.raw_executor is not self.raw_executor
                or execution._stager is not self.stager
                or execution._completions is not self.completions
                or execution._chat_toolchain is not self.chat_toolchain
                or execution._chat_toolchain_at_construction is not self.chat_toolchain
                or execution._stager._root != self.staging_root
                or execution._completions._root != self.staging_root
                or execution._execution_backend != (None if self.injected else "local")):
            raise DockingConsentError("consent_policy_unavailable")
        if self.chat_toolchain is not None:
            from src.docking.chat_toolchain import ChatDockingToolchain
            from src.docking.molecular_docking_adapter import MOLECULAR_DOCKING_ADAPTER_VERSION
            if (type(self.chat_toolchain) is not ChatDockingToolchain
                    or policy.tool_policy_digest != self.chat_toolchain.digest
                    or policy.adapter_contract_version != MOLECULAR_DOCKING_ADAPTER_VERSION):
                raise DockingConsentError("consent_policy_unavailable")
            self.chat_toolchain.require_current()
        private_output_root(self.root, staging_root=self.staging_root, excluded=self.excluded)
        return execution


def require_local_output_construction():
    # DockingExecution selects its backend from env. Reject BEFORE constructing
    # anything; do not rewrite process env or silently build a sandbox for C.
    if os.environ.get("MEDCHAT_DOCKING_EXECUTION_BACKEND", "local") != "local":
        raise DockingConsentError("consent_policy_unavailable")
