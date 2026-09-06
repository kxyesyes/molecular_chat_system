from __future__ import annotations

from collections import defaultdict

from .adapters import ToolAdapter


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolAdapter] = {}
        self._aliases: dict[str, str] = {}
        self._capabilities: dict[str, set[str]] = defaultdict(set)

    def register(self, adapter: ToolAdapter) -> None:
        name = adapter.spec.name
        if name in self._tools:
            raise ValueError(f"Tool already registered: {name}")
        self._tools[name] = adapter
        for alias in adapter.spec.aliases:
            if alias in self._aliases or alias in self._tools:
                raise ValueError(f"Tool alias already registered: {alias}")
            self._aliases[alias] = name
        for capability in adapter.spec.capabilities:
            self._capabilities[capability].add(name)

    def resolve(
        self,
        name: str,
        agent_name: str | None = None,
        require_available: bool = True,
    ) -> ToolAdapter:
        canonical = self._aliases.get(name, name)
        if canonical not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
        adapter = self._tools[canonical]
        owners = adapter.spec.owner_agents
        if agent_name and owners and agent_name not in owners:
            raise PermissionError(
                f"Agent {agent_name} is not authorized to use {canonical}"
            )
        if require_available and not adapter.health()["available"]:
            raise RuntimeError(
                f"Tool {canonical} is unavailable: {adapter.health()['message']}"
            )
        return adapter

    def by_capability(
        self, capability: str, require_available: bool = True
    ) -> list[ToolAdapter]:
        adapters = [
            self._tools[name] for name in sorted(self._capabilities.get(capability, set()))
        ]
        if require_available:
            adapters = [item for item in adapters if item.health()["available"]]
        return adapters

    def resolve_capability(
        self,
        capability: str,
        agent_name: str | None = None,
        require_available: bool = True,
    ) -> ToolAdapter:
        from src.agent.capabilities import get_capability

        spec = get_capability(capability)
        candidates = []
        for tool_name in spec.tool_names:
            try:
                candidates.append(
                    self.resolve(
                        tool_name,
                        agent_name=agent_name,
                        require_available=require_available,
                    )
                )
            except (KeyError, RuntimeError):
                continue
        if not candidates:
            raise RuntimeError(
                f"No available tool implements capability: {capability}"
            )
        return candidates[0]

    def validate_plan(
        self, tool_names: list[str], agent_name: str | None = None
    ) -> None:
        for tool_name in tool_names:
            self.resolve(tool_name, agent_name=agent_name, require_available=True)

    def health(self) -> list[dict]:
        return [self._tools[name].health() for name in sorted(self._tools)]

    def close(self) -> None:
        for name in sorted(self._tools):
            close = getattr(self._tools[name], "close", None)
            if callable(close):
                close()

    def as_mapping(self) -> dict[str, ToolAdapter]:
        return dict(self._tools)
