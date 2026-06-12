"""
RAG 检索增强生成服务模块
"""
import logging
import time
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


class RAGService:
    """Retrieval-Augmented Generation service for molecular data"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.molecular_rag = None
        self.is_initialized = False

    async def initialize(self):
        """Initialize the RAG system"""
        try:
            from src.rag.molecular_rag import MolecularRAG
            import os

            csv_path = self.config.get("rag", {}).get("csv_path", "data/canonical_moses_5w.csv")
            embedding_model = self.config.get("rag", {}).get("embedding_model", "nomic-embed-text:latest")
            vector_store_path = self.config.get("rag", {}).get("vector_store_path", "data/molecular_faiss_index")

            self.molecular_rag = MolecularRAG(
                csv_path=csv_path if os.path.exists(csv_path) else None,
                embedding_model=embedding_model,
                vector_store_path=vector_store_path
            )

            self.is_initialized = True
            logger.info("RAG system initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize RAG system: {e}")
            self.is_initialized = False

    async def search_similar_molecules(self, query: str, k: int = 2) -> List[Dict[str, Any]]:
        """Search for similar molecules based on query"""
        start_time = time.time()

        if not self.is_initialized or not self.molecular_rag:
            return []

        try:
            conditions, smiles = self.molecular_rag.parse_user_query(query)
            results = []

            # Search by properties
            if conditions:
                molecules = self.molecular_rag.search_by_properties(conditions, k)
                for mol in molecules:
                    mol_dict = mol.to_dict()
                    mol_dict['similarity_score'] = 1.0
                    results.append(mol_dict)

            # Search by similarity
            elif smiles:
                similarity_results = self.molecular_rag.search_by_similarity(smiles, k)
                for mol, score in similarity_results:
                    mol_dict = mol.to_dict()
                    mol_dict['similarity_score'] = 1.0 - score
                    results.append(mol_dict)

            # General search
            else:
                try:
                    import re
                    smiles_pattern = r'[A-Za-z0-9@+\-\[\]\(\)=#]{3,}'
                    matches = re.findall(smiles_pattern, query)
                    if matches:
                        potential_smiles = max(matches, key=len)
                        if len(potential_smiles) >= 3:
                            similarity_results = self.molecular_rag.search_by_similarity(potential_smiles, k)
                            for mol, score in similarity_results:
                                mol_dict = mol.to_dict()
                                mol_dict['similarity_score'] = 1.0 - score
                                results.append(mol_dict)
                except Exception:
                    pass

            search_time = time.time() - start_time
            logger.info(f"RAG search found {len(results)} molecules in {search_time:.2f}s")
            return results

        except Exception as e:
            logger.error(f"Error searching molecules: {e}")
            return []
