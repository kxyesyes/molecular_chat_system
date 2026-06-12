"""Inspect the local target-search SQLite database."""

from __future__ import annotations

import json

from .service import TargetSearchService


def main() -> None:
    service = TargetSearchService()
    stats = service.get_database_stats()
    health = service.get_database_health()
    payload = {
        "status": health["status"],
        "stats": stats,
        "health": {
            "database_exists": health["database_exists"],
            "cache_dir_exists": health["cache_dir_exists"],
            "tables": health["tables"],
            "target_count": health["target_count"],
            "structure_count": health["structure_count"],
            "cached_structure_count": health["cached_structure_count"],
            "missing_cache_count": health["missing_cache_count"],
            "missing_cache_files_sample": health["missing_cache_files"][:10],
            "notes": health["notes"],
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
