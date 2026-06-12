"""Inspect the reverse-target local database without loading fingerprints."""

from __future__ import annotations

import json
import os

from .health import inspect_reverse_target_database


def main() -> None:
    data_dir = os.environ.get("REVERSE_TARGET_DATA_DIR", "data/reverse_target")
    print(json.dumps(inspect_reverse_target_database(data_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
