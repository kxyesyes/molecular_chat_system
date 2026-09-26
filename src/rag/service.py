"""RAG service independent of Web application assembly."""
import asyncio
from contextlib import asynccontextmanager, contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import logging
from pathlib import Path
from threading import RLock
from typing import List, Dict, Any, Optional
from uuid import uuid4

import httpx
import pandas as pd
import numpy as np

from src.rag.retrieval import search_molecular_index, search_molecular_index_outcome
from src.rag.receipt import canonical_digest as _canonical_digest
from src.rag.index import (
    CURRENT_SCHEMA_VERSION,
    RAGIndexCompatibilityError,
    RAGIndexManifest,
    atomic_save_index_pair,
    file_sha256,
    immutable_index_snapshot,
    load_manifest,
    manifest_path,
    validate_manifest,
)

try:
    import faiss  # type: ignore
except Exception:  # pragma: no cover - optional dependency handling
    faiss = None

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _RequestConfig:
    source: Path
    store: str
    model: str
    endpoint: str


@dataclass(frozen=True)
class _Generation:
    epoch: int
    identity: str
    config: _RequestConfig
    frame: pd.DataFrame
    index: Any
    manifest: RAGIndexManifest
    source_digest: str
    index_digest: str


def _copy_frame(frame):
    # DataFrame.copy(deep=True) does not detach Python objects in object cells.
    result = frame.copy(deep=True)
    for column in result.select_dtypes(include=['object']).columns:
        result[column] = pd.Series(
            [deepcopy(value) for value in frame[column]],
            index=frame.index, dtype=frame[column].dtype,
        )
    return result


@asynccontextmanager
async def _operation_client(borrowed):
    client = borrowed if borrowed is not None else httpx.AsyncClient(timeout=60.0)
    cancelled = False
    try:
        yield client
    except asyncio.CancelledError:
        cancelled = True
        raise
    finally:
        if borrowed is None:
            cleanup = asyncio.create_task(client.aclose())
            try:
                while not cleanup.done():
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        cancelled = True
                # Retrieve failures as well; never leave an orphan cleanup task.
                cleanup.result()
            finally:
                # Cleanup errors must not turn cancellation into tolerant success.
                if cancelled:
                    raise asyncio.CancelledError


class RAGSystem:
    """Retrieval-Augmented Generation system for molecular data"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.embedding_model = None
        rag_config = self.config.get("rag", {})
        self.embedding_model_name = rag_config.get(
            "embedding_model",
            "nomic-embed-text:latest",
        )
        self.embedding_endpoint = rag_config.get(
            "embedding_endpoint", "http://localhost:11434/api/embeddings"
        )
        self._index_sha256 = None
        self.vector_index = None
        self.molecules_df: Optional[pd.DataFrame] = None
        self.csv_path = Path(
            rag_config.get("csv_path", "data/canonical_moses_5w.csv")
        )
        self.source_path = self.csv_path
        self.manifest: Optional[RAGIndexManifest] = None
        self.index_status = "uninitialized"
        self.is_initialized = False
        self._generation_lock = RLock()
        self._generation_epoch = 0
        self._generation = None
        self._closed = False

    def _effective_config(self):
        rag = self.config.get('rag', {})
        return _RequestConfig(
            Path(rag.get('csv_path', 'data/canonical_moses_5w.csv')),
            str(rag.get('vector_store_path', 'data/molecular_faiss_index')),
            rag.get('embedding_model', 'nomic-embed-text:latest'),
            rag.get('embedding_endpoint', 'http://localhost:11434/api/embeddings'),
        )

    def _invalidate(self, status):
        """Caller holds the generation lock; public injections remain unverified."""
        self._generation_epoch += 1
        self._generation = None
        self.is_initialized = False
        self.index_status = status

    def close(self):
        """Revoke readiness, without aborting or taking ownership of transports."""
        with self._generation_lock:
            if not self._closed:
                self._closed = True
                self._invalidate('closed')

    def _check_operation(self, epoch, config, source_digest):
        """Used under the lock, before commits and at query boundaries."""
        self._check_configuration(epoch, config)
        self._check_source(config, source_digest)

    def _check_configuration(self, epoch, config, *, expected_effective=None, expected_csv=None):
        """Cheap per-row guard; legacy public overrides have their own baseline."""
        if self._closed or epoch != self._generation_epoch:
            raise RAGIndexCompatibilityError('RAG generation changed')
        if expected_effective is None:
            expected_effective = config
        if expected_csv is None:
            expected_csv = config.source
        try:
            effective_config = self._effective_config()
        except (AttributeError, TypeError, ValueError):
            effective_config = None
        if (effective_config != expected_effective
                or self.embedding_model_name != config.model
                or self.embedding_endpoint != config.endpoint
                or self.source_path != config.source or self.csv_path != expected_csv):
            self._invalidate('incompatible: configuration changed')
            raise RAGIndexCompatibilityError('RAG configuration changed')

    def _check_source(self, config, source_digest):
        """Full-byte freshness check at load/commit/query boundaries, under lock."""
        try:
            coherent = file_sha256(config.source) == source_digest
        except OSError:
            coherent = False
        if not coherent:
            self._invalidate('incompatible: loaded source changed')
            raise RAGIndexCompatibilityError('RAG loaded source changed')

    async def initialize(self):
        """Only this byte-coherent path can certify an owned generation."""
        with self._generation_lock:
            if self._closed:
                raise RAGIndexCompatibilityError('RAG service is closed')
            self._invalidate('uninitialized')
            epoch = self._generation_epoch
            config = self._effective_config()
            borrowed = getattr(self, 'embedding_client', None)
            self.embedding_model_name = config.model
            self.embedding_endpoint = config.endpoint
            self.csv_path = self.source_path = config.source
            self.vector_index = self.manifest = self._index_sha256 = None
            self.molecules_df = None
        try:
            if faiss is None:
                raise RAGIndexCompatibilityError('unavailable: faiss is not installed')
            source_bytes = config.source.read_bytes()
            source_digest = hashlib.sha256(source_bytes).hexdigest()
            frame = _copy_frame(pd.read_csv(BytesIO(source_bytes)))
            with self._generation_lock:
                self._check_operation(epoch, config, source_digest)
            candidate, reason = self._load_candidate(frame, config, source_digest)
            with self._generation_lock:
                self._check_operation(epoch, config, source_digest)
            built = candidate is None
            if built:
                if frame.empty:
                    raise RAGIndexCompatibilityError(
                        f'incompatible: {reason}' if reason else 'unavailable: no molecular data')
                async with _operation_client(borrowed) as client:
                    async def embed(text):
                        with self._generation_lock:
                            self._check_configuration(epoch, config)
                        result = await self._embedding_async(client, config, text, tolerant=True)
                        with self._generation_lock:
                            self._check_configuration(epoch, config)
                        return result
                    candidate = await self._build_candidate(frame, config, source_digest, embed)
                if candidate is None:
                    raise RAGIndexCompatibilityError(
                        f'incompatible: {reason}' if reason else 'unavailable: no valid embeddings')
            index, manifest = candidate
            with self._generation_lock:
                self._check_operation(epoch, config, source_digest)
                if built:
                    manifest = atomic_save_index_pair(
                        index, Path(f'{config.store}.index'), manifest, faiss_module=faiss)
                self._check_operation(epoch, config, source_digest)
                self._validate_candidate(index, manifest, frame, config, manifest.index_sha256)
                generation = _Generation(epoch, uuid4().hex, config, frame, index,
                                         deepcopy(manifest), source_digest, manifest.index_sha256)
                # Prepare detached compatibility copies before publishing readiness.
                public_frame = _copy_frame(frame)
                public_index = faiss.clone_index(index)
                public_manifest = deepcopy(manifest)
                self._check_operation(epoch, config, source_digest)
                self.molecules_df = public_frame
                self.vector_index = public_index
                self.manifest = public_manifest
                self._index_sha256 = manifest.index_sha256
                self._generation = generation
                self.is_initialized = True
                self.index_status = ('rebuilt_after_incompatible' if reason else 'created') if built else 'loaded'
        except BaseException as error:
            with self._generation_lock:
                if epoch == self._generation_epoch and not self._closed:
                    status = str(error) if isinstance(error, RAGIndexCompatibilityError) else 'error: initialization failed'
                    self._invalidate(status)
            if not isinstance(error, Exception):
                raise
            logger.warning('RAG initialization unavailable')

    @staticmethod
    async def _embedding_async(client, config, text, *, tolerant=False):
        try:
            response = await client.post(config.endpoint, json={'model': config.model, 'prompt': text})
            response.raise_for_status()
            return np.asarray(response.json().get('embedding', []))
        except Exception:
            if not tolerant:
                raise
            logger.warning('RAG embedding unavailable')
            return np.array([])

    async def get_embedding(self, text: str) -> np.ndarray:
        """Legacy tolerant API; explicit injections are borrowed, never closed."""
        try:
            config = _RequestConfig(self.source_path, '', self.embedding_model_name, self.embedding_endpoint)
            async with _operation_client(getattr(self, 'embedding_client', None)) as client:
                return await self._embedding_async(client, config, text, tolerant=True)
        except Exception:
            logger.warning('RAG embedding unavailable')
            return np.array([])

    @staticmethod
    def _validate_candidate(index, manifest, frame, config, digest):
        validate_manifest(
            manifest, source_path=config.source, index_sha256=digest,
            embedding_model=config.model, vector_dimension=int(index.d),
            vector_count=int(index.ntotal), source_row_count=len(frame),
        )

    def _load_candidate(self, frame, config, source_digest=None):
        """Shared immutable-snapshot loader; no public state or certification."""
        index_path = Path(f"{config.store}.index")
        index_manifest_path = manifest_path(index_path)
        incompatible_reason: Optional[str] = None

        if index_path.is_file() and index_manifest_path.is_file():
            try:
                candidate_manifest = load_manifest(index_manifest_path)
                with immutable_index_snapshot(index_path) as index_snapshot_path:
                    candidate_index_sha256 = file_sha256(index_snapshot_path)
                    candidate_index = faiss.read_index(str(index_snapshot_path))
                    self._validate_candidate(candidate_index, candidate_manifest, frame,
                                             config, candidate_index_sha256)
                    if source_digest is not None and (
                            candidate_manifest.source_sha256 != source_digest
                            or Path(candidate_manifest.source_path).resolve() != config.source.resolve()
                            or candidate_manifest.builder_version != '1'):
                        raise RAGIndexCompatibilityError('RAG owned source or builder mismatch')
            except RAGIndexCompatibilityError as error:
                incompatible_reason = str(error)
            except Exception:
                incompatible_reason = (
                    "RAG index manifest incompatible: index cannot be read"
                )
            else:
                return (candidate_index, candidate_manifest), None
        elif index_path.exists() or index_manifest_path.exists():
            incompatible_reason = (
                "RAG index manifest incompatible: index and manifest must both exist"
            )

        return None, incompatible_reason

    async def _build_candidate(self, frame, config, source_digest, embed):
        """One scientific builder for owned initialize and legacy injected helpers."""

        # Preserve filtering, source-position mapping and cosine normalization.
        embeddings: List[np.ndarray] = []
        row_mapping: List[int] = []
        embedding_dimension: Optional[int] = None
        if frame is not None:
            for source_position, (_, row) in enumerate(frame.iterrows()):
                mol_text = f"SMILES: {row.get('SMILES', '')}"
                embedding = await embed(mol_text)

                embedding_array = np.asarray(embedding)
                if embedding_array.ndim != 1 or embedding_array.size == 0:
                    continue
                if not np.all(np.isfinite(embedding_array)):
                    logger.warning('Skipping non-finite embedding for source row %s', source_position)
                    continue
                current_dimension = int(embedding_array.shape[0])
                if embedding_dimension is None:
                    embedding_dimension = current_dimension
                elif current_dimension != embedding_dimension:
                    logger.warning('Skipping source row %s with incompatible embedding dimension', source_position)
                    continue
                embeddings.append(embedding_array.astype(np.float32))
                row_mapping.append(source_position)

                if source_position % 100 == 0:
                    logger.info('Processed %s/%s molecules', source_position, len(frame))

        if not embeddings:
            return None
        embeddings_array = np.vstack(embeddings)

        dimension = embeddings_array.shape[1]
        candidate_index = faiss.IndexFlatIP(dimension)  # Inner product for similarity

        embeddings_float32 = embeddings_array.astype(np.float32)
        faiss.normalize_L2(embeddings_float32)
        candidate_index.add(embeddings_float32)  # type: ignore

        candidate_manifest = RAGIndexManifest(
            schema_version=CURRENT_SCHEMA_VERSION,
            source_path=str(config.source),
            source_sha256=source_digest,
            index_sha256="",
            embedding_model=config.model,
            vector_dimension=dimension,
            vector_count=len(row_mapping),
            row_mapping=row_mapping,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        return candidate_index, candidate_manifest

    async def _legacy_candidate(self, vector_store_path, *, load):
        with self._generation_lock:
            if self._closed:
                raise RAGIndexCompatibilityError('RAG service is closed')
            self._invalidate('uninitialized')
            epoch = self._generation_epoch
            config = _RequestConfig(self.source_path, str(vector_store_path),
                                    self.embedding_model_name, self.embedding_endpoint)
            effective_config = self._effective_config()
            public_csv_path = self.csv_path
            borrowed = getattr(self, 'embedding_client', None)
            frame = self.molecules_df if self.molecules_df is not None else pd.DataFrame()
            embed = self.get_embedding
            default_transport = getattr(embed, '__func__', None) is RAGSystem.get_embedding
            self.vector_index = self.manifest = self._index_sha256 = None
        reason = None
        try:
            frame = _copy_frame(frame)
            source_bytes = config.source.read_bytes()
            source_digest = hashlib.sha256(source_bytes).hexdigest()
            source_frame = pd.read_csv(BytesIO(source_bytes))

            def check_configuration():
                self._check_configuration(epoch, config, expected_effective=effective_config,
                                          expected_csv=public_csv_path)

            async def guarded_embed(text):
                with self._generation_lock:
                    check_configuration()
                result = await embed(text)
                with self._generation_lock:
                    check_configuration()
                return result

            with self._generation_lock:
                check_configuration()
                self._check_source(config, source_digest)
                if not frame.reset_index(drop=True).equals(source_frame):
                    self._invalidate('incompatible: loaded source changed')
                    raise RAGIndexCompatibilityError('RAG loaded source changed')
            candidate, reason = self._load_candidate(frame, config) if load else (None, None)
            built = candidate is None
            if built and not frame.empty:
                if default_transport:
                    async with _operation_client(borrowed) as client:
                        async def captured_embed(text):
                            return await self._embedding_async(client, config, text, tolerant=True)
                        embed = captured_embed
                        candidate = await self._build_candidate(frame, config, source_digest, guarded_embed)
                else:
                    # Explicit legacy callback injections remain compatibility APIs.
                    candidate = await self._build_candidate(frame, config, source_digest, guarded_embed)
            with self._generation_lock:
                check_configuration()
                self._check_source(config, source_digest)
                if candidate is None:
                    self.index_status = (f'incompatible: {reason}' if reason else
                                         'unavailable: no molecular data' if frame.empty else
                                         'unavailable: no valid embeddings')
                    return
                index, manifest = candidate
                if built:
                    manifest = atomic_save_index_pair(index, Path(f'{config.store}.index'),
                                                      manifest, faiss_module=faiss)
                check_configuration()
                self._check_source(config, source_digest)
                self.vector_index, self.manifest = index, manifest
                self._index_sha256 = manifest.index_sha256
                self.index_status = ('rebuilt_after_incompatible' if reason else 'created') if built else 'loaded'
        except Exception:
            with self._generation_lock:
                if epoch == self._generation_epoch:
                    self.vector_index = self.manifest = None
                    self.index_status = f'incompatible: {reason}' if reason else 'error: index creation failed'
            logger.warning('RAG legacy index unavailable')

    async def _load_or_create_index(self, vector_store_path: str):
        """Unverified compatibility path, including public dataframe injections."""
        await self._legacy_candidate(vector_store_path, load=True)

    async def _create_index(self, vector_store_path: str):
        """Unverified compatibility builder using the same scientific algorithm."""
        await self._legacy_candidate(vector_store_path, load=False)

    def _require_retrieval_ready(self):
        if (not self.is_initialized or self.vector_index is None
                or self.manifest is None or self.molecules_df is None):
            raise RAGIndexCompatibilityError("RAG vector database is not fully initialized")

    def get_embedding_sync(self, text: str) -> np.ndarray:
        """Thread-safe sync transport; never reuse the async client's event loop."""
        config = _RequestConfig(self.source_path, '', self.embedding_model_name, self.embedding_endpoint)
        return self._embedding_sync(config, text)

    @staticmethod
    def _embedding_sync(config, text):
        with httpx.Client(timeout=60.0) as client:
            response = client.post(
                config.endpoint,
                json={"model": config.model, "prompt": text},
            )
            response.raise_for_status()
            return np.asarray(response.json().get("embedding", []))

    def search_similar_molecules_sync_with_receipt(self, query: str, k: int = 2):
        """Same-call source receipt, never reconstructed from public legacy state."""
        if not isinstance(query, str) or not query.strip():
            raise ValueError('RAG query must be a nonempty string')
        with self._generation_lock:
            generation = self._generation
            if generation is None or self._closed:
                raise RAGIndexCompatibilityError('RAG owned generation is unavailable')
            config = generation.config
            self._check_operation(generation.epoch, config, generation.source_digest)
            invocation = uuid4().hex
        with self._query_guard(generation):
            return self._query_receipt(generation, query, k, invocation)

    @contextmanager
    def _query_guard(self, generation):
        """Revoke observed changes even on failure, preserving the original error."""
        try:
            yield
        except BaseException:
            with self._generation_lock:
                try:
                    self._check_operation(generation.epoch, generation.config, generation.source_digest)
                except RAGIndexCompatibilityError:
                    pass
            raise
        else:
            with self._generation_lock:
                self._check_operation(generation.epoch, generation.config, generation.source_digest)

    def _query_receipt(self, generation, query, k, invocation):
        config = generation.config
        # Do not call an overridable legacy get_embedding_sync as source proof.
        embedding = self._embedding_sync(config, query)
        with self._generation_lock:
            self._check_operation(generation.epoch, config, generation.source_digest)
        outcome = search_molecular_index_outcome(
            index=generation.index, manifest=generation.manifest, molecules=generation.frame,
            source_path=config.source, index_sha256=generation.index_digest,
            embedding_model=config.model, embedding=embedding, k=k,
        )
        records = outcome['records']
        manifest = generation.manifest
        receipt = {
            'schema_version': '1', 'validation_revision': 'rag-owned-generation-v1',
            'invocation_id': invocation, 'generation_id': generation.identity,
            'input_sha256': hashlib.sha256(query.encode('utf-8')).hexdigest(),
            'source_path': manifest.source_path, 'source_sha256': generation.source_digest,
            'source_row_count': len(generation.frame), 'index_sha256': generation.index_digest,
            'row_mapping_sha256': _canonical_digest(manifest.row_mapping),
            'vector_dimension': int(generation.index.d), 'vector_count': int(generation.index.ntotal),
            'manifest_schema_version': manifest.schema_version, 'builder_version': manifest.builder_version,
            'embedding_model': config.model,
            'embedding_endpoint_sha256': hashlib.sha256(config.endpoint.encode('utf-8')).hexdigest(),
            'embedding_weights_verified': False, 'index_embedding_endpoint_sha256': None,
            'diagnostics': outcome['diagnostics'], 'result_sha256': _canonical_digest(records),
        }
        return deepcopy({'records': records, 'receipt': receipt})

    def _search_embedding(self, embedding, k):
        self._require_retrieval_ready()
        return search_molecular_index(
            index=self.vector_index, manifest=self.manifest, molecules=self.molecules_df,
            source_path=self.source_path, index_sha256=self._index_sha256,
            embedding_model=self.embedding_model_name, embedding=embedding, k=k,
        )

    def search_similar_molecules_sync(self, query: str, k: int = 2):
        """Agent adapter: propagate failures instead of reporting a false no-hit."""
        self._require_retrieval_ready()
        return self._search_embedding(self.get_embedding_sync(query), k)

    async def search_similar_molecules(self, query: str, k: int = 2) -> List[Dict[str, Any]]:
        """Keep the legacy list interface, using the same validated retrieval core."""
        try:
            self._require_retrieval_ready()
            return self._search_embedding(await self.get_embedding(query), k)
        except Exception:
            logger.warning("RAG retrieval unavailable; no unverified records returned")
            return []
