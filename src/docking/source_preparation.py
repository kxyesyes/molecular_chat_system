"""Canonical source preparation in the existing retained child lifecycle.

Separate from offline preparation's closed operation map. No Web session,
paths, URL, caller source dictionary or arbitrary callback is accepted. The Web
coordinator must still bind/revalidate current S03/root/generation before using
any result. The same original deadline and 60-second child cap cover all work.
Keep the supplied PreparationOwner until physical settlement, even on cancel.
"""

from __future__ import annotations

import re
import time

from . import chat_preparation as lifecycle


_ACCESSION = re.compile(r"(?:[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})\Z")


def _source_candidate(pdb_id, accession, taxonomy, deadline):
    """Fixed child entry; canonical downloads followed by independent comparison.

    Stream capacity, discovery, compiler and returned raw-byte IPC share one
    logical ledger. Nothing is refunded between phases. This is not an OS RSS
    guarantee. The parent can terminate this process even if DNS is blocked.
    """
    from . import receptor_projection_pdb as pdb
    from . import rcsb_source as compiler
    from src.target_search import downloader

    def current():
        if time.monotonic() >= deadline:
            raise TimeoutError()

    comparing = False
    try:
        current()
        budget = pdb.AllocationBudget(min(compiler.MAX_LOGICAL_BYTES, 512 * 1024 * 1024))
        # Pair retains <=32 MiB plus <=16 MiB stream/copy; CCD retains <=16
        # MiB alongside the pair, plus <=1 MiB stream/copy. 64 MiB reserves
        # both bounded peaks, bytearray growth and transport/framing buffers
        # before either fetch allocates.
        # Compiler input reservations additionally retain these bytes; the
        # deliberate conservative overlap never depends on garbage collection.
        budget.reserve(64 * 1024 * 1024)
        pair = downloader.fetch_rcsb_source_pair(pdb_id, deadline=deadline)
        current()
        comparing = True
        ids = compiler._discover_components(pair["cif_bytes"], pdb_id, budget=budget)
        comparing = False
        current()
        components = downloader.fetch_rcsb_component_files(ids, deadline=deadline)
        current()
        comparing = True
        compared = compiler._compile(pair["pdb_bytes"], pair["cif_bytes"], components,
            pdb_id, {"uniprot_id": accession, "taxonomy_id": taxonomy}, budget=budget)
        comparing = False
        current()
        # Compiler already charged descriptor/evidence serialization. Keep the
        # exact downloaded originals for later owner-bound audit artifacts too.
        raw_size = len(pair["pdb_bytes"]) + len(pair["cif_bytes"]) + sum(len(raw) for raw in components.values())
        budget.reserve(65536 + 4 * raw_size)
        current()
        return {**compared, "pdb_bytes": pair["pdb_bytes"], "cif_bytes": pair["cif_bytes"],
                "component_files": components, "input_ready": False, "execution_authorized": False,
                "source_authentication": "requires_live_owner_binding"}
    except TimeoutError:
        return lifecycle._failure("PREPARATION_TIMEOUT")
    except compiler._Invalid as exc:
        reason = exc.args[0] if len(exc.args) == 1 and type(exc.args[0]) is str else "cif_invalid"
        return compiler._error(reason if reason in compiler.SAFE_REASONS else "cif_invalid")
    except pdb.ProjectionInvalid as exc:
        return compiler._error("resource_limit" if exc.args == ("resource_limit",) else "source_invalid")
    except (ValueError, RuntimeError, KeyError, IndexError, UnicodeError, MemoryError, ImportError):
        # Mirror the pure compiler's fixed parsing-error boundary ONLY while
        # comparing. A download failure (also RuntimeError) is not a CIF error.
        if comparing:
            return compiler._error("cif_invalid")
    except Exception:
        pass
    return {"ok": False, "code": "SOURCE_PREPARATION_FAILED",
            "message": "Official source preparation failed."}


async def run_source_preparation(*, pdb_id, target, deadline, owner):
    """One fixed canonical-fetch/compare child, never execution authorization."""
    if (type(owner) is not lifecycle.PreparationOwner or owner._claimed
            or type(pdb_id) is not str or re.fullmatch(r"[0-9][A-Z0-9]{3}", pdb_id) is None
            or type(target) is not dict or len(target) != 2
            or not lifecycle._valid_deadline(deadline)):
        return lifecycle._failure("PREPARATION_INPUT_INVALID")
    captured = {}
    try:
        for key, value in target.items():
            if type(key) is not str or key not in {"uniprot_id", "taxonomy_id"} or len(captured) >= 2:
                return lifecycle._failure("PREPARATION_INPUT_INVALID")
            if key == "uniprot_id":
                if type(value) is not str or len(value) > 10 or _ACCESSION.fullmatch(value) is None:
                    return lifecycle._failure("PREPARATION_INPUT_INVALID")
            elif type(value) is not int or not 1 <= value <= 2**31 - 1:
                return lifecycle._failure("PREPARATION_INPUT_INVALID")
            captured[key] = value
        if len(captured) != 2:
            return lifecycle._failure("PREPARATION_INPUT_INVALID")
    except RuntimeError:
        return lifecycle._failure("PREPARATION_INPUT_INVALID")
    owner._claimed = True
    work_deadline = min(deadline, time.monotonic() + lifecycle.CHILD_MAX_SECONDS)
    if work_deadline <= time.monotonic():
        return {**lifecycle._failure("PREPARATION_TIMEOUT"), "lifecycle": owner.snapshot()}
    return await lifecycle._run_owned_producer(_source_candidate,
        (pdb_id, captured["uniprot_id"], captured["taxonomy_id"], work_deadline),
        work_deadline=work_deadline, owner=owner)
