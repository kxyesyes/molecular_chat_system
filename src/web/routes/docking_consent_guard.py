"""Read-only durable C membership gate shared by legacy docking/report routes."""
from fastapi import HTTPException

from src.task_runtime import manager as manager_module
from src.task_runtime.store import TaskStore


class DockingConsentGuard:
    def __init__(self, task_runtime=None):
        self.task_runtime = task_runtime

    def current_runtime(self):
        if self.task_runtime is None:
            return None
        try:
            runtime = self.task_runtime() if callable(self.task_runtime) else self.task_runtime
            if runtime is None:
                raise RuntimeError('Configured runtime unavailable')
            return runtime
        except Exception as exc:
            raise HTTPException(503, 'Task runtime unavailable') from exc

    def is_consent(self, task_id):
        try:
            runtime = self.current_runtime()
            stores = [manager_module.get_task_manager().store]
            if runtime is not None:
                stores.append(runtime.store)
            membership = []
            for store in stores:
                if not isinstance(store, TaskStore):
                    raise TypeError('Missing task store')
                result = store.is_docking_consent_task(task_id)
                if type(result) is not bool:
                    raise TypeError('Invalid task classifier')
                membership.append(result)
            # Do not short circuit: every configured authority must be available.
            return any(membership)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(503, 'Task ownership storage unavailable') from exc

    def preflight(self):
        self.is_consent('legacy-history-storage-probe')

    def require_legacy(self, task_id):
        if not self.is_legacy(task_id):
            raise HTTPException(404, '对接任务不存在')

    def is_legacy(self, task_id):
        """Exclude C membership and actual task collisions before any legacy hit."""
        if self.is_consent(task_id):
            return False
        try:
            runtime = self.current_runtime()
            if runtime is None:
                # With no configured runtime there is only one task authority.
                return True
            manager_store = manager_module.get_task_manager().store
            stores = [manager_store]
            if runtime is not None:
                stores.append(runtime.store)
            exists = []
            for store in stores:
                if not isinstance(store, TaskStore):
                    raise TypeError('Missing task store')
                try:
                    # Store metadata only: never call runtime/manager refresh APIs.
                    store.get(task_id)
                except KeyError:
                    exists.append(False)
                else:
                    exists.append(True)
            collision = len(stores) == 2 and all(exists) and not (
                stores[0] is stores[1] or stores[0].db_path.samefile(stores[1].db_path)
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(503, 'Task ownership storage unavailable') from exc
        # A consent-only reservation can appear during the collision lookup.
        late_consent = self.is_consent(task_id)
        return not (collision or late_consent)

    def require_unambiguous_durable(self, runtime, task_id):
        """Recheck the authority actually read, not a replacement binding."""
        DockingConsentGuard(runtime).require_legacy(task_id)
        if self.current_runtime() is not runtime:
            raise HTTPException(503, 'Task runtime unavailable')


def report_consent_guard(app):
    # setup_api_routes registers docking first, sharing the exact runtime binding
    # without a second global runtime lookup. Standalone reports remain legacy-only.
    try:
        guard = app.state.legacy_docking_consent_guard
    except AttributeError:
        return DockingConsentGuard()
    if not isinstance(guard, DockingConsentGuard):
        raise HTTPException(503, 'Task ownership storage unavailable')
    return guard
