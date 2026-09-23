"""RAG service independent of Web application assembly."""
from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

import httpx
import pandas as pd
import numpy as np

from src.rag.retrieval import search_molecular_index
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

    async def initialize(self):
        """Initialize the RAG system"""
        try:
            if faiss is None:
                logger.warning("FAISS is not installed; RAG retrieval is disabled.")
                self.index_status = "unavailable: faiss is not installed"
                self.is_initialized = False
                return

            # Load embedding model
            embedding_model_name = self.config.get("rag", {}).get("embedding_model", "nomic-embed-text:latest")
            logger.info(f"Loading embedding model: {embedding_model_name}")

            # For Ollama embeddings
            self.embedding_client = httpx.AsyncClient(timeout=60.0)
            self.embedding_model_name = embedding_model_name

            # Load molecular data
            self.csv_path = Path(
                self.config.get("rag", {}).get(
                    "csv_path",
                    "data/canonical_moses_5w.csv",
                )
            )
            self.source_path = self.csv_path
            if self.csv_path.exists():
                self.molecules_df = pd.read_csv(self.csv_path)
                logger.info(
                    f"Loaded {len(self.molecules_df)} molecules from {self.csv_path}"
                )
            else:
                logger.warning(f"Molecular data file not found: {self.csv_path}")
                self.molecules_df = pd.DataFrame()

            # Load or create vector index
            vector_store_path = self.config.get("rag", {}).get("vector_store_path", "data/molecular_faiss_index")
            await self._load_or_create_index(vector_store_path)

            self.is_initialized = self.vector_index is not None and self.manifest is not None
            if self.is_initialized:
                logger.info("RAG system initialized successfully")
            else:
                logger.warning(
                    "RAG system initialized without a usable index: %s",
                    self.index_status,
                )

        except Exception as e:
            logger.error(f"Failed to initialize RAG system: {e}")
            self.index_status = "error: initialization failed"
            self.is_initialized = False

    async def get_embedding(self, text: str) -> np.ndarray:
        """Get embedding for text using Ollama"""
        try:
            response = await self.embedding_client.post(
                self.embedding_endpoint,
                json={
                    "model": self.embedding_model_name,
                    "prompt": text
                }
            )

            if response.status_code == 200:
                result = response.json()
                return np.array(result.get("embedding", []))
            else:
                logger.error(f"Embedding API error: {response.status_code}")
                return np.array([])

        except Exception as e:
            logger.error(f"Error getting embedding: {e}")
            return np.array([])

    async def _load_or_create_index(self, vector_store_path: str):
        """Load existing vector index or create new one"""
        index_path = Path(f"{vector_store_path}.index")
        index_manifest_path = manifest_path(index_path)
        incompatible_reason: Optional[str] = None
        self.vector_index = None
        self.manifest = None
        self._index_sha256 = None

        if index_path.is_file() and index_manifest_path.is_file():
            try:
                candidate_manifest = load_manifest(index_manifest_path)
                with immutable_index_snapshot(index_path) as index_snapshot_path:
                    candidate_index_sha256 = file_sha256(index_snapshot_path)
                    candidate_index = faiss.read_index(str(index_snapshot_path))
                    validate_manifest(
                        candidate_manifest,
                        source_path=self.source_path,
                        index_sha256=candidate_index_sha256,
                        embedding_model=self.embedding_model_name,
                        vector_dimension=int(candidate_index.d),
                        vector_count=int(candidate_index.ntotal),
                        source_row_count=(
                            len(self.molecules_df)
                            if self.molecules_df is not None
                            else 0
                        ),
                    )
            except RAGIndexCompatibilityError as error:
                incompatible_reason = str(error)
            except Exception:
                incompatible_reason = (
                    "RAG index manifest incompatible: index cannot be read"
                )
            else:
                self.vector_index = candidate_index
                self.manifest = candidate_manifest
                self._index_sha256 = candidate_index_sha256
                self.index_status = "loaded"
                logger.info("Loaded compatible vector index and manifest")
                return
        elif index_path.exists() or index_manifest_path.exists():
            incompatible_reason = (
                "RAG index manifest incompatible: index and manifest must both exist"
            )

        if incompatible_reason:
            self.index_status = f"incompatible: {incompatible_reason}"
            logger.warning("Rejected existing RAG index: %s", incompatible_reason)

        if self.molecules_df is not None and not self.molecules_df.empty:
            await self._create_index(vector_store_path)
            if self.vector_index is not None and self.manifest is not None:
                if incompatible_reason:
                    self.index_status = "rebuilt_after_incompatible"
                return

        self.vector_index = None
        self.manifest = None
        if incompatible_reason:
            self.index_status = f"incompatible: {incompatible_reason}"
        elif self.index_status == "uninitialized":
            self.index_status = "unavailable: no molecular data"
            logger.warning("No molecular data available to create index")

    async def _create_index(self, vector_store_path: str):
        """Create vector index from molecular data"""
        try:
            logger.info("Creating vector index from molecular data...")

            # Create embeddings for molecules
            embeddings: List[np.ndarray] = []
            row_mapping: List[int] = []
            embedding_dimension: Optional[int] = None
            if self.molecules_df is not None:
                for source_position, (_, row) in enumerate(
                    self.molecules_df.iterrows()
                ):
                    # Create text representation of molecule
                    mol_text = f"SMILES: {row.get('SMILES', '')}"
                    embedding = await self.get_embedding(mol_text)

                    embedding_array = np.asarray(embedding)
                    if embedding_array.ndim != 1 or embedding_array.size == 0:
                        continue
                    if not np.all(np.isfinite(embedding_array)):
                        logger.warning(
                            "Skipping non-finite embedding for source row %s",
                            source_position,
                        )
                        continue
                    current_dimension = int(embedding_array.shape[0])
                    if embedding_dimension is None:
                        embedding_dimension = current_dimension
                    elif current_dimension != embedding_dimension:
                        logger.warning(
                            "Skipping source row %s with incompatible embedding dimension",
                            source_position,
                        )
                        continue
                    embeddings.append(embedding_array.astype(np.float32))
                    row_mapping.append(source_position)

                    # Use counter for progress tracking
                    if source_position % 100 == 0:
                        logger.info(
                            f"Processed {source_position}/{len(self.molecules_df)} molecules"
                        )

            if embeddings:
                embeddings_array = np.vstack(embeddings)

                # Create FAISS index
                dimension = embeddings_array.shape[1]
                candidate_index = faiss.IndexFlatIP(dimension)  # Inner product for similarity

                # Normalize embeddings for cosine similarity
                embeddings_float32 = embeddings_array.astype(np.float32)
                faiss.normalize_L2(embeddings_float32)
                # Add embeddings to index - FAISS add method takes the array directly
                # Ignore type checking errors for FAISS methods
                candidate_index.add(embeddings_float32)  # type: ignore

                candidate_manifest = RAGIndexManifest(
                    schema_version=CURRENT_SCHEMA_VERSION,
                    source_path=str(self.source_path),
                    source_sha256=file_sha256(self.source_path),
                    index_sha256="",
                    embedding_model=self.embedding_model_name,
                    vector_dimension=dimension,
                    vector_count=len(row_mapping),
                    row_mapping=row_mapping,
                    created_at=datetime.now(timezone.utc).isoformat(),
                )
                persisted_manifest = atomic_save_index_pair(
                    candidate_index,
                    Path(f"{vector_store_path}.index"),
                    candidate_manifest,
                    faiss_module=faiss,
                )
                self.vector_index = candidate_index
                self.manifest = persisted_manifest
                self._index_sha256 = persisted_manifest.index_sha256
                self.index_status = "created"

                logger.info(f"Created and saved vector index with {len(embeddings)} embeddings")
            else:
                logger.error("No valid embeddings created")
                self.vector_index = None
                self.manifest = None
                self.index_status = "unavailable: no valid embeddings"

        except Exception as e:
            logger.error(f"Error creating vector index: {e}")
            self.vector_index = None
            self.manifest = None
            self.index_status = "error: index creation failed"

    def _require_retrieval_ready(self):
        if (not self.is_initialized or self.vector_index is None
                or self.manifest is None or self.molecules_df is None):
            raise RAGIndexCompatibilityError("RAG vector database is not fully initialized")

    def get_embedding_sync(self, text: str) -> np.ndarray:
        """Thread-safe sync transport; never reuse the async client's event loop."""
        with httpx.Client(timeout=60.0) as client:
            response = client.post(
                self.embedding_endpoint,
                json={"model": self.embedding_model_name, "prompt": text},
            )
            response.raise_for_status()
            return np.asarray(response.json().get("embedding", []))

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
