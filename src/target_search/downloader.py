"""Download and cache protein structure files for target-search demo."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import requests

from .database import absolute_from_project, get_connection, relative_to_project

logger = logging.getLogger(__name__)

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
    def __init__(self, project_root: Optional[Path | str] = None):
        self.project_root = Path(project_root).resolve() if project_root else Path(__file__).resolve().parents[2]

    def prepare_structure_file(self, structure: dict, requested_format: Optional[str] = None) -> dict:
        file_format = (requested_format or structure.get("file_format") or "cif").lower()
        local_path = self._local_path_for_format(structure, file_format)
        absolute_path = absolute_from_project(local_path, self.project_root)

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
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            absolute_path.write_bytes(response.content)
            self._mark_downloaded(structure["id"], local_path, download_url=url, file_format=file_format)
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
                response = requests.get(url, timeout=45)
                response.raise_for_status()
                absolute_path.write_bytes(response.content)
                self._mark_downloaded(structure["id"], local_path, download_url=url, file_format=file_format)
                return {
                    "success": True,
                    "file_path": str(absolute_path),
                    "local_file_path": local_path,
                    "file_format": file_format,
                    "message": "Downloaded and cached AlphaFold structure file.",
                }
            except requests.RequestException as exc:
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
            response = requests.get(url, timeout=45)
            response.raise_for_status()
            absolute_path.write_bytes(response.content)
            self._mark_downloaded(structure["id"], local_path, download_url=url, file_format=file_format)
            return {
                "success": True,
                "file_path": str(absolute_path),
                "local_file_path": local_path,
                "file_format": file_format,
                "message": "Downloaded and cached AlphaFold structure file.",
            }
        except requests.RequestException as exc:
            logger.warning("AlphaFold download failed for %s: %s", structure.get("structure_id"), exc)
            raise StructureDownloadError(f"Failed to download AlphaFold model as {file_format}: {exc}") from exc

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
