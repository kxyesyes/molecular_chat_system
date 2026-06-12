# Target Search Local Database

This document records the reproducible local deployment flow for the target-search demo database.

## Current Curated Scope

- Demo/common seed targets: 15 seed targets in `data/target_db/seed_targets.csv`.
- Expanded common targets: 52 curated non-PDE targets in `data/target_db/common_targets.csv`.
- Expanded common structure index: 313 curated non-PDE structure records in `data/target_db/common_structures.csv`.
- PDE family targets: 24 curated PDE targets in `data/target_db/pde_targets.csv`.
- PDE structure index: 705 curated PDE structure records in `data/target_db/pde_structures.csv`.
- Structure files are stored under `data/target_db/cache/`.
- SQLite stores metadata and relative file paths only.

## Rebuild Database

Run from the project root:

```powershell
python -m src.target_search.seed
```

The seed script loads these files when present:

- `data/target_db/seed_targets.csv`
- `data/target_db/seed_structures.csv`
- `data/target_db/common_targets.csv`
- `data/target_db/common_structures.csv`
- `data/target_db/pde_targets.csv`
- `data/target_db/pde_structures.csv`

To force a clean rebuild from code, use:

```powershell
python - <<'PY'
from src.target_search.seed import rebuild_database
print(rebuild_database())
PY
```

## Export PDE Curated CSVs

If the SQLite database has been updated and you want to refresh the rebuildable PDE CSVs:

```powershell
python -m src.target_search.export_curated --family pde
```

This writes:

- `data/target_db/pde_targets.csv`
- `data/target_db/pde_structures.csv`

To refresh the expanded non-PDE common target CSVs:

```powershell
python -m src.target_search.export_curated --family common
```

This writes:

- `data/target_db/common_targets.csv`
- `data/target_db/common_structures.csv`

## Health Checks

Check local database and cache coverage:

```powershell
python - <<'PY'
from src.target_search.service import TargetSearchService
service = TargetSearchService()
print(service.get_database_stats())
print(service.get_database_health())
PY
```

Expected deployment-level checks for the current curated database:

- `target_count` is 76.
- `structure_count` is 1018.
- `missing_cache_count` is 0.
- `pde_target_count` is 24.
- `pde_structure_count` is 705.
- `pde_cache_coverage` is 1.0.

## API Smoke Test

After starting the web app, these endpoints should respond:

- `GET /api/target-db/stats`
- `GET /api/target-db/health`
- `GET /api/target-db/pde-overview`
- `GET /api/target-db/search?query=PDE5A`
- `GET /api/target-db/search?query=phosphodiesterase`
- `GET /api/target-db/search?query=BTK`

The page entry remains:

- `/target-search`
