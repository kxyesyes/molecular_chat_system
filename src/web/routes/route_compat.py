"""Small helpers for legacy route-registration compatibility.

The application path supplies explicit dependencies.  Older direct callers may
still pass the former support module, so lookups stay lazy to preserve its
post-registration monkeypatch behavior without making each route reach into
the support module itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


_MISSING = object()


@dataclass(frozen=True)
class DependencySpec:
    """Describe one explicit dependency and its legacy fallback."""

    explicit: Any
    legacy_attribute: str
    label: str
    default: Any = _MISSING


def lazy_dependency(
    explicit: Any,
    legacy_support: Any,
    attribute: str,
    *,
    label: str,
    default: Any = _MISSING,
) -> Callable[[], Any]:
    """Return a getter with explicit-dependency precedence.

    ``legacy_support`` is intentionally inspected on every call.  This keeps
    the compatibility behavior of old direct route callers whose support
    attributes were historically patchable after registration.
    """

    def resolve() -> Any:
        if explicit is not None:
            return explicit
        if legacy_support is not None:
            try:
                return getattr(legacy_support, attribute)
            except AttributeError:
                pass
        if default is not _MISSING:
            return default
        raise RuntimeError(f"{label} dependency is not configured")

    return resolve


def dependency_getters(
    legacy_support: Any,
    **specs: DependencySpec,
) -> dict[str, Callable[[], Any]]:
    """Build route dependency getters at one compatibility boundary.

    The application path supplies explicit values in each specification.  A
    legacy support object is consulted lazily only when that value is absent,
    preserving older callers that patch support attributes after registration.
    Keeping this assembly here prevents individual route modules from
    reimplementing the compatibility policy.
    """

    getters: dict[str, Callable[[], Any]] = {}
    for name, spec in specs.items():
        if not isinstance(spec, DependencySpec):
            raise TypeError(f"{name} must be a DependencySpec")
        getters[name] = lazy_dependency(
            spec.explicit,
            legacy_support,
            spec.legacy_attribute,
            label=spec.label,
            default=spec.default,
        )
    return getters


__all__ = ["DependencySpec", "dependency_getters", "lazy_dependency"]
