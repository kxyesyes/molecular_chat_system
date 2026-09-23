"""Read-only registration preflight; never loads weights or probes providers."""
from src.agent.capabilities import CAPABILITY_CATALOG
from src.agent.capabilities.catalog import TOOL_ALIASES
from src.agent.workflows import WorkflowCatalog
from .factory import TOOL_AGENT_OWNERS

REQUIRED_TOOLS = frozenset({
    "property_calculator", "drug_likeness_assessment", "admet_predictor",
    "llm_molecular_generator", "candidate_ranker",
})
# Absence is a reported runtime limitation, not a malformed installation.
OPTIONAL_TOOLS = frozenset({
    "target_database_search", "reverse_target_predictor", "activity_predictor",
    "molecular_docking", "prepare_receptor", "prepare_ligand", "run_docking",
    "get_docking_result", "rag_search", "rag_database_search",
})


def audit_registration(registry, specialists, *, catalog=None):
    catalog = catalog or WorkflowCatalog()
    errors = []
    unavailable = set()
    for alias, canonical in TOOL_ALIASES.items():
        try:
            adapter = registry.resolve(canonical, require_available=False)
        except KeyError:
            continue  # Both may legitimately be absent optional implementations.
        try:
            alias_adapter = registry.resolve(alias, require_available=False)
        except KeyError:
            errors.append(f"Required alias not registered: {alias}")
        else:
            if alias_adapter is not adapter:
                errors.append(f"Alias resolves to different tool: {alias}")
    referenced = set(REQUIRED_TOOLS)
    for policy in catalog.policies:
        referenced.update(policy.allowed_tools)
    for capability in CAPABILITY_CATALOG:
        referenced.update(capability.tool_names)
    for adapter in registry.as_mapping().values():
        referenced.add(adapter.spec.name)
        referenced.update(adapter.spec.aliases)
    for name in sorted(referenced):
        owner = TOOL_AGENT_OWNERS.get(name)
        specialist = specialists.get(owner)
        if not owner or specialist is None or specialist.name != owner:
            errors.append(f"Unknown owner for tool: {name}")
            continue
        if name not in specialist.allowed_tools:
            errors.append(f"Specialist allowlist missing tool: {name}")
        try:
            adapter = registry.resolve(name, agent_name=owner, require_available=False)
        except KeyError:
            if name in OPTIONAL_TOOLS:
                unavailable.add(name)
            else:
                errors.append(f"Required tool not registered: {name}")
        except PermissionError:
            errors.append(f"Registry ownership mismatch: {name}")
        else:
            if adapter.spec.owner_agents != {owner}:
                errors.append(f"Registry ownership mismatch: {name}")
            if adapter.health()["available"] is False:
                unavailable.add(name)
    capabilities = []
    for capability in CAPABILITY_CATALOG:
        registered = []
        for name in capability.tool_names:
            try:
                adapter = registry.resolve(name, require_available=False)
            except KeyError:
                continue
            registered.append(name)
            if capability.name not in adapter.spec.capabilities:
                errors.append(f"Capability not indexed: {capability.name}/{name}")
        capabilities.append({
            "name": capability.name, "registered_tools": registered,
            # Registration alone is not evidence of model/provider readiness.
            "readiness": "unavailable" if not registered or all(
                name in unavailable for name in registered) else "not_probed",
        })
    return {"valid": not errors, "errors": errors,
            "unavailable_tools": sorted(unavailable), "capabilities": capabilities}
