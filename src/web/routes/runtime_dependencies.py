"""Narrow runtime dependencies used when composing HTTP domain routes.

The legacy registrar still owns a few patchable helpers for direct callers and
tests.  This module turns those helpers into individual callable dependencies;
domain route modules never receive or inspect the registrar module itself.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any


class _DynamicLogger:
    def __init__(self, owner: Any):
        self._owner = owner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._owner.logger, name)


class _DynamicInvoker:
    def __init__(self, owner: Any):
        self._owner = owner

    async def __call__(self, func, *args, **kwargs):
        return await self._owner._invoke_in_threadpool(func, *args, **kwargs)


class _DynamicReportValidator:
    def __init__(self, owner: Any):
        self._owner = owner

    def __call__(self, *args, **kwargs):
        return self._owner._validate_report_base64_payload(*args, **kwargs)


class _DynamicUploadReader:
    def __init__(self, owner: Any):
        self._owner = owner

    async def __call__(self, *args, **kwargs):
        return await self._owner._read_upload_limited(*args, **kwargs)


class _DynamicTempfile:
    def __init__(self, owner: Any):
        self._owner = owner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._owner.tempfile, name)


class _DynamicActivityInvoker:
    def __init__(self, activity_routes: Any):
        self._activity_routes = activity_routes

    async def __call__(self, *args, **kwargs):
        return await self._activity_routes._invoke_activity_with_budget(*args, **kwargs)


class _DynamicEnvGetter:
    def __init__(self, owner: Any):
        self._owner = owner

    def __call__(self, name, default=None):
        return self._owner.os.getenv(name, default)


class _DynamicExecutorFactory:
    def __init__(self, owner: Any):
        self._owner = owner

    def __call__(self, *args, **kwargs):
        return self._owner.ThreadPoolExecutor(*args, **kwargs)


class _DynamicSupportCallable:
    def __init__(self, owner: Any, attribute: str):
        self._owner = owner
        self._attribute = attribute

    def __call__(self, *args, **kwargs):
        return getattr(self._owner, self._attribute)(*args, **kwargs)


def build_route_dependencies(owner: Any, activity_routes: Any) -> SimpleNamespace:
    """Build one narrow dependency object from the legacy registrar.

    ``owner`` is retained only by these private compatibility callables.  The
    object passed to each route setup function exposes individual operations,
    never the registrar or a generic support container.
    """

    return SimpleNamespace(
        logger=_DynamicLogger(owner),
        invoker=_DynamicInvoker(owner),
        report_validator=_DynamicReportValidator(owner),
        upload_reader=_DynamicUploadReader(owner),
        tempfile=_DynamicTempfile(owner),
        activity_invoker=_DynamicActivityInvoker(activity_routes),
        env_getter=_DynamicEnvGetter(owner),
        executor_factory=_DynamicExecutorFactory(owner),
        pharm3d_limit=_DynamicSupportCallable(owner, "_get_pharm3d_candidate_pool_limit"),
        pharm3d_timeout=_DynamicSupportCallable(owner, "_get_pharm3d_timeout"),
        pharm3d_runner=_DynamicSupportCallable(owner, "_run_pharm3d_job"),
        pharm3d_fallback=_DynamicSupportCallable(owner, "_build_pharm3d_fallback"),
        docking_limits=_DynamicSupportCallable(owner, "_validate_docking_limits"),
        docking_warnings=_DynamicSupportCallable(owner, "_normalize_warning_strings"),
        docking_int_env=_DynamicSupportCallable(owner, "_get_int_env"),
        api_success=_DynamicSupportCallable(owner, "api_success"),
    )


__all__ = ["build_route_dependencies"]
