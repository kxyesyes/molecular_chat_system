"""Evidence-only target identity and structure resolution."""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Optional

from .remote_clients import (
    AlphaFoldClient,
    BoundedJsonClient,
    RcsbClient,
    RemoteSourceError,
    UniProtClient,
)


_RESOLUTION_STATUSES = frozenset(
    {"resolved", "not_found", "ambiguous", "unavailable"}
)


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("JSON object keys must be strings")
        return MappingProxyType(
            {key: _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise TypeError("resolution evidence must contain only JSON-safe values")


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


@dataclass(frozen=True)
class TargetResolution:
    status: str
    target: Optional[Mapping[str, Any]] = None
    structures: tuple[Mapping[str, Any], ...] = ()
    warnings: tuple[str, ...] = ()
    lookup_path: tuple[str, ...] = ()
    retryable: bool = False
    source: Optional[str] = None
    code: Optional[str] = None

    def __post_init__(self) -> None:
        if self.status not in _RESOLUTION_STATUSES:
            raise ValueError("unsupported target resolution status")
        if self.target is not None and not isinstance(self.target, Mapping):
            raise TypeError("target must be a mapping or None")
        if any(not isinstance(item, Mapping) for item in self.structures):
            raise TypeError("structures must contain mappings")
        if any(not isinstance(item, str) for item in self.warnings):
            raise TypeError("warnings must contain strings")
        if any(not isinstance(item, str) for item in self.lookup_path):
            raise TypeError("lookup_path must contain strings")
        if not isinstance(self.retryable, bool):
            raise TypeError("retryable must be a boolean")
        if self.retryable and self.status != "unavailable":
            raise ValueError("only unavailable resolutions can be retryable")
        for name, value in (("source", self.source), ("code", self.code)):
            if value is not None and (
                not isinstance(value, str) or not value.strip()
            ):
                raise TypeError(f"{name} must be non-empty text or None")
        object.__setattr__(
            self,
            "target",
            None if self.target is None else _freeze_json(self.target),
        )
        object.__setattr__(
            self,
            "structures",
            tuple(_freeze_json(item) for item in self.structures),
        )
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(self, "lookup_path", tuple(self.lookup_path))

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "target": _thaw_json(self.target),
            "structures": _thaw_json(self.structures),
            "warnings": list(self.warnings),
            "lookup_path": list(self.lookup_path),
            "retryable": self.retryable,
            "source": self.source,
            "code": self.code,
        }


class AuthoritativeTargetResolver:
    """Resolve target identity and structures without generative inference."""

    def __init__(
        self,
        uniprot_client: Optional[UniProtClient] = None,
        rcsb_client: Optional[RcsbClient] = None,
        alphafold_client: Optional[AlphaFoldClient] = None,
    ) -> None:
        self._owned_clients: list[Any] = []
        self._uniprot = uniprot_client or self._new_owned_client(UniProtClient)
        self._rcsb = rcsb_client or self._new_owned_client(RcsbClient)
        self._alphafold = alphafold_client or self._new_owned_client(AlphaFoldClient)
        self._closed = False

    def _new_owned_client(self, factory):
        client = factory(BoundedJsonClient())
        self._owned_clients.append(client)
        return client

    def resolve(
        self, query: str, organism: str | int = "Homo sapiens"
    ) -> TargetResolution:
        lookup_path = ["UniProt"]
        try:
            target = self._uniprot.resolve(query, organism)
        except RemoteSourceError as exc:
            return self._failure(exc, lookup_path)
        if target is None:
            return TargetResolution(
                status="not_found", lookup_path=tuple(lookup_path)
            )

        accession = str(target["uniprot_id"])
        lookup_path.append("RCSB_PDB")
        try:
            structures = self._rcsb.search(accession)
        except RemoteSourceError as exc:
            return self._failure(exc, lookup_path)
        if structures:
            return TargetResolution(
                status="resolved",
                target=target,
                structures=tuple(structures),
                lookup_path=tuple(lookup_path),
            )

        lookup_path.append("AlphaFold")
        try:
            predicted = self._alphafold.lookup(accession)
        except RemoteSourceError as exc:
            return self._failure(exc, lookup_path)
        return TargetResolution(
            status="resolved",
            target=target,
            structures=() if predicted is None else (predicted,),
            lookup_path=tuple(lookup_path),
        )

    @staticmethod
    def _failure(
        error: RemoteSourceError, lookup_path: list[str]
    ) -> TargetResolution:
        status = "ambiguous" if error.code == "ambiguous_match" else "unavailable"
        return TargetResolution(
            status=status,
            warnings=(f"{error.source}:{error.code}",),
            lookup_path=tuple(lookup_path),
            retryable=error.retryable,
            source=error.source,
            code=error.code,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for client in self._owned_clients:
            client.close()

    def __enter__(self) -> "AuthoritativeTargetResolver":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
