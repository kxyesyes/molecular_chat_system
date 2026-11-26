"""Execute one confirmed homepage batch and serve its session-owned pose receipts."""
import asyncio
from contextlib import nullcontext
import hashlib
import json
from uuid import uuid4

from src.docking.adapters.base import CommandOwnershipScope
from src.docking.molecular_docking_service import SharedBatchReceptor
from src.system.env import get_int_env
from src.system.model_lifecycle import finish_on_cancel
from src.task_runtime.completion import CompletionError
from src.task_runtime.database import connection
from src.task_runtime.docking_consent import DockingConsentError, canonical_json
from src.task_runtime.docking_execution import _manifest_input_hash
from src.task_runtime.models import TaskStatus
from src.task_runtime.store import utc_now
from .routes.docking_routes import run_bounded_docking_batch


def _digest(content):
    return hashlib.sha256(content).hexdigest()


_RESULT_COLUMNS = "ordinal,smiles,status,best_energy,total_poses,error"
_TASK_TYPE = "home_docking_batch"


class HomeDockingBatchExecution:
    def __init__(self, task_runtime):
        self.runtime = task_runtime
        self.store = task_runtime.store
        self.policy = task_runtime._docking_consent_policy
        self._admission = asyncio.Lock()
        self._active = {}
        self._closing = False
        self._slots = asyncio.Semaphore(get_int_env("MEDCHAT_DOCKING_BATCH_CONCURRENCY", 1))

    async def submit(self, *, task_id, owner_session_id, pdb_id, receptor_bytes, ligands,
                     parameters, receptor_name="receptor.pdb", admission_guard=None, binding_digest=None):
        """The caller supplies the approved source evidence under its admission guard."""
        parameters = json.loads(canonical_json(parameters))
        ligands = [dict(row) for row in ligands]
        return await finish_on_cancel(self._submit(task_id, owner_session_id, pdb_id,
            receptor_name, receptor_bytes, ligands, parameters, admission_guard or nullcontext, binding_digest))

    async def _submit(self, task_id, owner, pdb_id, receptor_name, receptor, ligands, config, guard, binding_digest):
        async with self._admission:
            if self._closing or self.runtime._closing:
                raise DockingConsentError("consent_policy_unavailable")
            ordinals = [r["ordinal"] for r in ligands]
            if (not 1 <= len(ligands) <= get_int_env("MEDCHAT_DOCKING_MAX_BATCH_LIGANDS", 100)
                    or len(set(ordinals)) != len(ordinals)
                    or any(type(n) is not int or n < 1 for n in ordinals)
                    or receptor_name != "receptor.pdb"):
                raise ValueError("invalid batch manifest")
            manifest = dict(pdb_id=pdb_id, receptor_sha256=_digest(receptor), parameters=config,
                binding_digest=binding_digest,
                tool_policy_digest=self.policy.tool_policy_digest,
                ligands=[dict(ordinal=r["ordinal"], smiles=r["smiles"], sha256=_digest(r["sdf_bytes"]))
                         for r in ligands])
            jobs = await asyncio.to_thread(self._admit, task_id, owner, manifest, guard)
            if jobs:
                cached = SharedBatchReceptor(manifest["receptor_sha256"])
                receipts = {}

                async def run_item(index, job, event):
                    try:
                        await asyncio.to_thread(self._mark_running, task_id, job["ordinal"])
                        result, receipt = await asyncio.to_thread(self._run_row, job, receptor,
                            ligands[index-1]["sdf_bytes"], config, cached, event)
                        receipts[index] = receipt
                        return result
                    except Exception:
                        return dict(success=False, error="Docking input or tool verification failed.")

                async def record(index, row):
                    await finish_on_cancel(asyncio.to_thread(self._record, task_id,
                        jobs[index-1]["ordinal"], row, receipts.get(index)))

                queue = asyncio.create_task(run_bounded_docking_batch(jobs, run_item,
                    semaphore=self._slots, on_row=record, max_item_seconds=self.policy.vina_limit_seconds,
                    max_batch_seconds=self.policy.operation_limit_seconds))
                settled = asyncio.create_task(self._settle(task_id, owner, queue))
                self._active[task_id] = (owner, queue, settled)
            return await self.status(task_id, owner)

    def _admit(self, task_id, owner, manifest, guard):
        jobs = None
        try:
            with guard() as validate_locked, connection(self.store.db_path) as conn:
                conn.execute("BEGIN IMMEDIATE")
                if validate_locked is not None:
                    validate_locked()
                jobs = self._reserve(conn, task_id, owner, manifest)
        except Exception:
            # Source/deadline guards also check on exit, after the DB commit.
            # Only a batch reserved by this invocation can need settlement.
            if jobs:
                try:
                    self._finish(task_id, owner, "failed")
                except KeyError:
                    pass  # The failed transaction never committed a parent.
            raise
        return jobs

    def _reserve(self, conn, task_id, owner, manifest):
        digest = _digest(canonical_json(manifest))
        existing = conn.execute("SELECT owner_session_id,task_type,submission_digest FROM tasks "
                                "WHERE task_id=?", (task_id,)).fetchone()
        if existing:
            if existing["owner_session_id"] != owner or existing["task_type"] != _TASK_TYPE:
                raise KeyError(task_id)
            if existing["submission_digest"] != digest:
                raise ValueError("batch manifest changed")
            return None
        execution = self.runtime._require_consent_output()
        if execution._chat_toolchain is None and not execution._raw_executor_injected:
            raise DockingConsentError("consent_policy_unavailable")
        now = utc_now()
        conn.execute("""INSERT INTO tasks(task_id,task_type,status,backend,phase,attempt,
            owner_session_id,submission_digest,created_at,updated_at,started_at)
            VALUES(?,?,'running','local','running',1,?,?,?,?,?)""",
            (task_id, _TASK_TYPE, owner, digest, now, now, now))
        for event in ("task_created", "task_started"):
            self.store._append_event(conn, task_id, event, {"task_type": _TASK_TYPE},
                                     is_terminal=False, created_at=now)
        conn.execute("INSERT INTO home_docking_batches VALUES(?,?,?,?)", (task_id,
            manifest["pdb_id"], self.policy.runtime_generation, canonical_json(manifest).decode()))
        jobs = []
        for position, row in enumerate(manifest["ligands"]):
            child_id = uuid4().hex
            conn.execute("""INSERT INTO home_docking_batch_rows
                (task_id,position,ordinal,smiles,child_id) VALUES(?,?,?,?,?)""",
                (task_id, position, row["ordinal"], row["smiles"], child_id))
            jobs.append(dict(**row, child_id=child_id, ligand_name=str(row["ordinal"]), input_type="file"))
        return jobs

    def _mark_running(self, task_id, ordinal):
        with connection(self.store.db_path) as conn:
            conn.execute("UPDATE home_docking_batch_rows SET status='running' "
                         "WHERE task_id=? AND ordinal=? AND status='queued'", (task_id, ordinal))

    def _run_row(self, job, receptor, ligand, config, cached, event):
        execution = self.runtime._require_consent_output()
        child = job["child_id"]
        manifest = self.runtime.stager.stage(child, "receptor.pdb", receptor, "ligand.sdf", ligand, None, config)
        expected_config = dict(config, center=tuple(config["center"]), size=tuple(config["size"]))
        def check(inputs, phase):
            if event.is_set():
                raise DockingConsentError("consent_cancelled")
            if (inputs.task_id != child or inputs.receptor_sha256 != cached.receptor_sha256
                    or inputs.ligand_sha256 != job["sha256"] or inputs.ligand_mode != "file"
                    or inputs.config != expected_config):
                raise DockingConsentError("consent_binding_mismatch")
            if phase == "dispatch":
                if self.runtime._require_consent_output() is not execution:
                    raise DockingConsentError("consent_policy_unavailable")
        scope = CommandOwnershipScope(task_id=child, output_root=execution._allowed_output_root, cancel_event=event)
        result = execution.run_verified(child, manifest, cancel_event=event, consent_guard=check,
                                        command_scope=scope, batch_receptor=cached)
        if not result.get("success"):
            error = result.get("error") or {}
            message = error.get("message") if isinstance(error, dict) else str(error)
            return dict(success=False, error=message or "Docking did not produce a verified result."), None
        saved = self.runtime.stager.load_verified(child, manifest)
        if (saved["receptor"]["sha256"] != cached.receptor_sha256 or saved["ligand"]["sha256"] != job["sha256"]
                or saved["config"] != config):
            raise ValueError("batch input changed")
        receipt = dict(input_hash=_manifest_input_hash(saved), config_hash=saved["config_hash"])
        completion = execution._completions.load_verified(child, **receipt)
        if (not completion.real_execution or completion.demo_mode or completion.fallback_used
                or completion.pose_count > config["num_modes"] or completion.to_dict() != result["completion"]):
            raise ValueError("batch completion mismatch")
        receipt["completion_digest"] = _digest(canonical_json(completion.to_dict()))
        return dict(success=True, best_pose=dict(binding_energy=completion.best_energy),
                    total_poses=completion.pose_count), receipt

    def _record(self, task_id, ordinal, result, receipt):
        success = result["status"] == "completed" and receipt is not None
        with connection(self.store.db_path) as conn:
            conn.execute("""UPDATE home_docking_batch_rows SET status=?,error=?,best_energy=?,
                total_poses=?,receipt_json=? WHERE task_id=? AND ordinal=? AND status IN ('queued','running')""",
                (result["status"], result["error"], result["best_energy"] if success else None,
                 result["total_poses"] if success else None, json.dumps(receipt) if success else None,
                 task_id, ordinal))

    async def _settle(self, task_id, owner, queue):
        try:
            summary = await queue
            pending = "timed_out" if summary["timed_out"] else "failed"
        except asyncio.CancelledError:
            pending = "cancelled"
        except Exception:
            pending = "failed"
        try:
            await asyncio.to_thread(self._finish, task_id, owner, pending)
        finally:
            self._active.pop(task_id, None)

    def _finish(self, task_id, owner, pending):
        reason = {"failed": "Docking execution was interrupted.", "cancelled": "Docking was cancelled.",
                  "timed_out": "Batch docking exceeded its time limit."}[pending]
        with connection(self.store.db_path) as conn:
            conn.execute("UPDATE home_docking_batch_rows SET status=?,error=? "
                         "WHERE task_id=? AND status IN ('queued','running')", (pending, reason, task_id))
            counts = conn.execute("SELECT COUNT(*) AS total,SUM(status='completed') AS completed "
                                  "FROM home_docking_batch_rows WHERE task_id=?", (task_id,)).fetchone()
        record = self.store.get_owned(task_id, owner)
        completed, total = counts["completed"], counts["total"]
        if record.status is TaskStatus.CANCEL_REQUESTED:
            status, terminal = "cancelled", TaskStatus.CANCELED
        elif completed == total:
            status, terminal = "completed", TaskStatus.SUCCEEDED
        elif completed:
            status, terminal = "partial", TaskStatus.SUCCEEDED
        elif pending == "timed_out":
            status, terminal = "timed_out", TaskStatus.TIMED_OUT
        else:
            status, terminal = "failed", TaskStatus.FAILED
        if not self.store.finish(task_id, terminal, result=dict(status=status, **counts)):
            # Cancellation may win between reading the parent and committing the terminal event.
            self.store.finish(task_id, TaskStatus.CANCELED, result=dict(status="cancelled", **counts))

    def _view(self, task_id, owner):
        record = self.store.get_owned(task_id, owner)
        if record.task_type != _TASK_TYPE:
            raise KeyError(task_id)
        with connection(self.store.db_path) as conn:
            batch = conn.execute("SELECT pdb_id,runtime_generation FROM home_docking_batches "
                                 "WHERE task_id=?", (task_id,)).fetchone()
            rows = conn.execute(f"SELECT {_RESULT_COLUMNS} FROM home_docking_batch_rows "
                                "WHERE task_id=? ORDER BY position", (task_id,)).fetchall()
        if (record.status in {TaskStatus.RUNNING, TaskStatus.CANCEL_REQUESTED}
                and batch["runtime_generation"] != self.policy.runtime_generation):
            self._finish(task_id, owner, "cancelled" if record.status is TaskStatus.CANCEL_REQUESTED else "failed")
            return self._view(task_id, owner)
        completed = sum(row["status"] == "completed" for row in rows)
        status = {TaskStatus.CANCELED: "cancelled", TaskStatus.TIMED_OUT: "timed_out",
                  TaskStatus.SUCCEEDED: "completed" if completed == len(rows) else "partial"
                  }.get(record.status, record.status.value)
        for row in rows:
            row["download_path"] = (f"/api/agent/docking/tasks/{task_id}/batch-poses/{row['ordinal']}"
                                    if row["status"] == "completed" else None)
        return dict(task_id=task_id, pdb_id=batch["pdb_id"], status=status, rows=rows,
                    completed=completed, total=len(rows),
                    failed=sum(row["status"] in {"failed","timeout","timed_out","cancelled"} for row in rows))

    async def status(self, task_id, owner_session_id):
        return await asyncio.to_thread(self._view, task_id, owner_session_id)

    async def pose(self, task_id, owner_session_id, ordinal):
        def read():
            with connection(self.store.db_path) as conn:
                row = conn.execute("""SELECT child_id,receipt_json FROM home_docking_batch_rows
                    JOIN tasks USING(task_id) WHERE task_id=? AND owner_session_id=?
                    AND ordinal=? AND home_docking_batch_rows.status='completed'""",
                    (task_id, owner_session_id, ordinal)).fetchone()
            if row is None:
                raise KeyError(task_id)
            try:
                return self.runtime._docking_private_output.completions.read_verified_artifact(
                    row["child_id"], artifact_id="pose", **json.loads(row["receipt_json"]))["content"]
            except CompletionError:
                raise KeyError(task_id) from None
        return await asyncio.to_thread(read)

    async def cancel(self, task_id, owner_session_id):
        async with self._admission:
            await self.status(task_id, owner_session_id)
            active = self._active.get(task_id)
            if active is not None:
                await asyncio.to_thread(self.store.request_cancel, task_id, owner_session_id=owner_session_id)
                active[1].cancel()
        return await self.status(task_id, owner_session_id)

    async def shutdown(self):
        async with self._admission:
            self._closing = True
            active = list(self._active.items())
            for task_id, (owner, queue, _) in active:
                await asyncio.to_thread(self.store.request_cancel, task_id, owner_session_id=owner)
                queue.cancel()
        if active:
            await finish_on_cancel(asyncio.gather(*(owned[2] for _, owned in active)))
