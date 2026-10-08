"""Small helpers for legacy route-registration compatibility.

The application path supplies explicit dependencies.  Older direct callers may
still pass the former support module, so lookups stay lazy to preserve its
post-registration monkeypatch behavior without making each route reach into
the support module itself.
"""

from __future__ import annotations

from typing import Any, Callable


def lazy_dependency(
    explicit: Any,
    legacy_support: Any,
    attribute: str,
    *,
    label: str,
    default: Any = None,
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
        if default is not None:
            return default
        raise RuntimeError(f"{label} dependency is not configured")

    return resolve


__all__ = ["lazy_dependency"]
