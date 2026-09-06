"""Download and cache protein structure files for target-search demo."""

from __future__ import annotations

import gzip
import logging
import os
import re
from datetime import timedelta
from pathlib import Path
from typing import Optional, TYPE_CHECKING
from uuid import uuid4

import requests

from .database import (
    absolute_from_project,
    get_cache_dir,
    get_connection,
    relative_to_project,
)

if TYPE_CHECKING:
    from .cache import TargetCacheRepository

logger = logging.getLogger(__name__)

MAX_STRUCTURE_DOWNLOAD_BYTES = 50 * 1024 * 1024
VALIDATION_READ_BYTES = 2 * 1024 * 1024
DEFAULT_CACHE_PREFIX = Path("data") / "target_db" / "cache"

RCSB_FORMAT_EXTENSIONS = {
    "cif": "cif",
    "pdb": "pdb",
    "cif.gz": "cif.gz",
    "pdb.gz": "pdb.gz",
}

ALPHAFOLD_FORMAT_EXTENSIONS = {
    "cif": "cif",
    "pdb": "pdb",
}


class StructureDownloadError(RuntimeError):
    """Raised when a structure file cannot be prepared."""


class StructureDownloader:
    def __init__(
        self,
        project_root: Optional[Path | str] = None,
        *,
        cache: Optional["TargetCacheRepository"] = None,
    ):
        self.project_root = Path(project_root).resolve() if project_root else Path(__file__).resolve().parents[2]
        if cache is None:
            from .cache import TargetCacheRepository

            cache = TargetCacheRepository(self.project_root)
        self.cache = cache

    def prepare_structure_file(self, structure: dict, requested_format: Optional[str] = None) -> dict:
        file_format = (requested_format or structure.get("file_format") or "cif").lower()
        local_path = self._local_path_for_format(structure, file_format)
        absolute_path = self._absolute_cache_path(local_path)

        if absolute_path.exists():
            self._mark_downloaded(structure["id"], local_path)
            return {
                "success": True,
                "file_path": str(absolute_path),
                "local_file_path": local_path,
                "file_format": file_format,
                "message": "Using cached structure file.",
            }

        source = str(structure.get("source") or "")
        if source == "RCSB_PDB":
            return self._download_rcsb(structure, file_format, local_path, absolute_path)
        if source == "AlphaFold":
            return self._prepare_alphafold(structure, file_format, local_path, absolute_path)

        raise StructureDownloadError(f"Unsupported structure source: {source}")

    def _local_path_for_format(self, structure: dict, requested_format: str) -> str:
        original = str(structure.get("local_file_path") or "").strip()
        if not original:
            raise StructureDownloadError("Structure record does not define local_file_path.")

        current_format = str(structure.get("file_format") or "").lower()
        if requested_format == current_format:
            return original

        path = Path(original)
        suffix = "." + requested_format
        if requested_format.endswith(".gz"):
            suffix = "." + requested_format
        if path.name.endswith(".cif.gz") or path.name.endswith(".pdb.gz"):
            stem = path.name.rsplit(".", 2)[0]
            return (path.parent / f"{stem}{suffix}").as_posix()
        return path.with_suffix(suffix).as_posix()

    def _download_rcsb(self, structure: dict, file_format: str, local_path: str, absolute_path: Path) -> dict:
        if file_format not in RCSB_FORMAT_EXTENSIONS:
            raise StructureDownloadError(f"Unsupported RCSB format: {file_format}")

        pdb_id = str(structure.get("structure_id") or "").upper()
        url = structure.get("download_url")
        if not url or file_format != str(structure.get("file_format") or "").lower():
            url = f"https://files.rcsb.org/download/{pdb_id}.{RCSB_FORMAT_EXTENSIONS[file_format]}"

        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            response = requests.get(url, timeout=30, stream=True)
            response.raise_for_status()
            absolute_path, local_path = self._publish_managed_response(
                response, structure, file_format
            )
            self._mark_downloaded(
                structure["id"],
                absolute_path,
                download_url=url,
                file_format=file_format,
            )
            return {
                "success": True,
                "file_path": str(absolute_path),
                "local_file_path": local_path,
                "file_format": file_format,
                "message": "Downloaded and cached structure file.",
            }
        except requests.RequestException as exc:
            logger.warning("RCSB download failed for %s: %s", pdb_id, exc)
            raise StructureDownloadError(f"Failed to download {pdb_id}.{file_format}: {exc}") from exc

    def _prepare_alphafold(self, structure: dict, file_format: str, local_path: str, absolute_path: Path) -> dict:
        if absolute_path.exists():
            self._mark_downloaded(structure["id"], local_path, file_format=file_format)
            return {
                "success": True,
                "file_path": str(absolute_path),
                "local_file_path": local_path,
                "file_format": file_format,
                "message": "Using cached AlphaFold structure file.",
            }
        if file_format not in ALPHAFOLD_FORMAT_EXTENSIONS:
            raise StructureDownloadError(f"Unsupported AlphaFold format: {file_format}")

        candidate_urls = self._alphafold_urls(structure, file_format)
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        last_error: Optional[Exception] = None
        for url in candidate_urls:
            try:
                response = requests.get(url, timeout=45, stream=True)
                response.raise_for_status()
                absolute_path, local_path = self._publish_managed_response(
                    response, structure, file_format
                )
                self._mark_downloaded(
                    structure["id"],
                    absolute_path,
                    download_url=url,
                    file_format=file_format,
                )
                return {
                    "success": True,
                    "file_path": str(absolute_path),
                    "local_file_path": local_path,
                    "file_format": file_format,
                    "message": "Downloaded and cached AlphaFold structure file.",
                }
            except (requests.RequestException, StructureDownloadError) as exc:
                last_error = exc
                logger.warning("AlphaFold download failed for %s from %s: %s", structure.get("structure_id"), url, exc)
        raise StructureDownloadError(f"Failed to download AlphaFold model as {file_format}: {last_error}")

    def _alphafold_urls(self, structure: dict, file_format: str) -> list[str]:
        preferred_url = structure.get("download_url")
        model_id = self._alphafold_model_id(structure)
        urls = []
        if preferred_url:
            urls.append(str(preferred_url))
        for version in ("v6", "v5", "v4", "v3", "v2"):
            urls.append(f"https://alphafold.ebi.ac.uk/files/{model_id}-model_{version}.{ALPHAFOLD_FORMAT_EXTENSIONS[file_format]}")
        return list(dict.fromkeys(urls))

    def _alphafold_model_id(self, structure: dict) -> str:
        structure_id = str(structure.get("structure_id") or "").strip()
        if structure_id.startswith("AF-"):
            return structure_id.split("-model_", 1)[0]
        local = Path(str(structure.get("local_file_path") or ""))
        stem = local.name
        if stem.startswith("AF-"):
            return stem.split("-model_", 1)[0].rsplit(".", 1)[0]
        raise StructureDownloadError("AlphaFold record does not expose a UniProt model id.")

    def _alphafold_url(self, structure: dict, file_format: str) -> str:
        return self._alphafold_urls(structure, file_format)[0]

    def _legacy_alphafold_download(self, structure: dict, file_format: str, local_path: str, absolute_path: Path) -> dict:
        try:
            url = self._alphafold_url(structure, file_format)
            response = requests.get(url, timeout=45, stream=True)
            response.raise_for_status()
            self._publish_response(response, absolute_path, file_format)
            self._mark_downloaded(structure["id"], local_path, download_url=url, file_format=file_format)
            return {
                "success": True,
                "file_path": str(absolute_path),
                "local_file_path": local_path,
                "file_format": file_format,
                "message": "Downloaded and cached AlphaFold structure file.",
            }
        except (requests.RequestException, StructureDownloadError) as exc:
            logger.warning("AlphaFold download failed for %s: %s", structure.get("structure_id"), exc)
            raise StructureDownloadError(f"Failed to download AlphaFold model as {file_format}: {exc}") from exc

    def _absolute_cache_path(self, local_path: str | Path) -> Path:
        path = Path(local_path)
        if path.is_absolute():
            return path
        try:
            cache_relative = path.relative_to(DEFAULT_CACHE_PREFIX)
        except ValueError:
            return absolute_from_project(path, self.project_root)
        return get_cache_dir(self.project_root) / cache_relative

    def _publish_response(
        self,
        response,
        final_path: Path,
        file_format: str,
    ) -> None:
        content_length = self._content_length(response)
        if (
            content_length is not None
            and content_length > MAX_STRUCTURE_DOWNLOAD_BYTES
        ):
            raise StructureDownloadError(
                "Structure download exceeds the maximum allowed size"
            )

        final_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = final_path.with_name(
            f".{final_path.name}.{uuid4().hex}.tmp"
        )
        total_bytes = 0
        try:
            with temporary_path.open("xb") as handle:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    total_bytes += len(chunk)
                    if total_bytes > MAX_STRUCTURE_DOWNLOAD_BYTES:
                        raise StructureDownloadError(
                            "Structure download exceeds the maximum allowed size"
                        )
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())

            if total_bytes == 0:
                raise StructureDownloadError("Structure download returned no data")
            self._validate_structure_file(temporary_path, file_format)
            temporary_path.replace(final_path)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

    def _publish_managed_response(
        self,
        response,
        structure: dict,
        file_format: str,
    ) -> tuple[Path, str]:
        normalized_format = file_format.lower()
        coordinate_suffix = f".{normalized_format}"
        cache_root = get_cache_dir(self.project_root)
        staged_relative = Path(".incoming") / f"{uuid4().hex}.download"
        staged_path = cache_root.joinpath(*staged_relative.parts)
        try:
            self._publish_response(response, staged_path, normalized_format)
            source = str(structure.get("source") or "")
            structure_id = str(structure.get("structure_id") or "")
            evidence = self.cache.publish_coordinate(
                self._coordinate_cache_key(source, structure_id, normalized_format),
                source,
                f"{structure_id}:{normalized_format}",
                staged_path=staged_relative.as_posix(),
                coordinate_suffix=coordinate_suffix,
                ttl=timedelta(days=90),
            )
        finally:
            staged_path.unlink(missing_ok=True)
        cache_relative_path = str(evidence.payload["path"])
        absolute_path = cache_root.joinpath(*Path(cache_relative_path).parts)
        return absolute_path, relative_to_project(absolute_path, self.project_root)

    @staticmethod
    def _coordinate_cache_key(source: str, structure_id: str, file_format: str) -> str:
        return f"coordinate:{source}:{structure_id}:{file_format}"

    @staticmethod
    def _content_length(response) -> Optional[int]:
        headers = getattr(response, "headers", {}) or {}
        raw_value = headers.get("Content-Length") or headers.get("content-length")
        if raw_value in (None, ""):
            return None
        try:
            return max(0, int(raw_value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _validate_structure_file(path: Path, file_format: str) -> None:
        normalized_format = file_format.lower()
        try:
            if normalized_format.endswith(".gz"):
                with gzip.open(path, "rb") as handle:
                    sample = handle.read(VALIDATION_READ_BYTES)
                normalized_format = normalized_format.removesuffix(".gz")
            else:
                with path.open("rb") as handle:
                    sample = handle.read(VALIDATION_READ_BYTES)
        except (OSError, EOFError) as exc:
            raise StructureDownloadError(
                f"Structure file validation failed: {exc}"
            ) from exc

        text = sample.decode("utf-8", errors="replace")
        if normalized_format == "cif":
            valid = bool(
                re.search(r"(?m)^data_\S+", text)
                and ("_atom_site." in text or re.search(r"(?m)^(ATOM|HETATM)\b", text))
            )
        elif normalized_format == "pdb":
            valid = bool(
                re.search(r"(?m)^(HEADER|TITLE|MODEL|ATOM  |HETATM)", text)
            )
        else:
            valid = False
        if not valid:
            raise StructureDownloadError(
                f"Structure file validation failed for format {file_format}"
            )

    def _mark_downloaded(
        self,
        structure_db_id: int,
        local_file_path: str,
        download_url: Optional[str] = None,
        file_format: Optional[str] = None,
    ) -> None:
        fields = ["local_file_path = ?", "is_downloaded = 1", "updated_at = datetime('now')"]
        params: list[object] = [relative_to_project(absolute_from_project(local_file_path, self.project_root), self.project_root)]
        if download_url:
            fields.append("download_url = ?")
            params.append(download_url)
        if file_format:
            fields.append("file_format = ?")
            params.append(file_format)
        params.append(structure_db_id)
        conn = get_connection(self.project_root)
        try:
            conn.execute(f"UPDATE target_structures SET {', '.join(fields)} WHERE id = ?", params)
            conn.commit()
        finally:
            conn.close()
