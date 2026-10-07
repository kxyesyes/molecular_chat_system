"""Persistent JSON-lines worker for ADMET-AI 1.4.0.

Run this with a Python 3.10 environment containing exactly ``admet-ai==1.4.0``.
The parent Agent process can keep its existing Torch/RG-MPNN environment; model
weights and the ADMET-AI cache remain inside this worker process.
"""

from __future__ import annotations

import json
from contextlib import redirect_stderr, redirect_stdout
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    try:
        from src.admet.backend import ADMETAIBackend

        # Chemprop/RDKit progress output is not part of the JSON-lines
        # protocol.  Keep stdout reserved for handshake/result envelopes.
        with redirect_stdout(sys.stderr), redirect_stderr(sys.stderr):
            backend = ADMETAIBackend.from_installed_package()
        print(json.dumps({
            "ready": True,
            "version": backend.version,
            "weights_id": backend.weights_id,
            "data_version": backend.data_version,
            "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        }), flush=True)
    except Exception as exc:
        print(json.dumps({"ready": False, "error": str(exc)}), flush=True)
        return 1

    for line in sys.stdin:
        try:
            request = json.loads(line)
            smiles = request["smiles"]
            molecule_ids = request["molecule_ids"]
            with redirect_stdout(sys.stderr), redirect_stderr(sys.stderr):
                rows = backend.predict_batch(smiles, molecule_ids)
            print(json.dumps({"ok": True, "rows": rows}, ensure_ascii=False), flush=True)
        except Exception as exc:
            print(json.dumps({"ok": False, "error": str(exc)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
