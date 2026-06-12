#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RAG 检索工具 - 允许 Agent 主动从本地知识库/分子数据库中检索相似分子
"""

import logging
import requests
import numpy as np
from typing import Dict, Any, List
from .base_tool import BaseMolecularTool

logger = logging.getLogger(__name__)

class RAGSearchTool(BaseMolecularTool):
    """
    RAG 本地知识库检索工具
    """
    
    def __init__(self):
        super().__init__(
            name="rag_database_search",
            description="Searches the local molecular knowledge base (RAG database) for similar molecules or text context. Useful when asked to find similar molecules in the database, or when looking up known structural properties of molecules. Input should be the search query or molecule SMILES."
        )

    def should_use(self, query: str) -> bool:
        """检查查询是否需要RAG数据库搜索"""
        query_lower = query.lower()
        keywords = ['database', 'knowledge base', 'similar molecule', 'search in database', '数据库', '库里', '类似分子', '检索', '查一下']
        return any(keyword in query_lower for keyword in keywords)

    def execute(self, query: str, **kwargs) -> Dict[str, Any]:
        """执行RAG搜索"""
        logger.info(f"RAGSearchTool triggered with query: {query}")
        
        try:
            from src.web.app import app_instance
            if not app_instance or not hasattr(app_instance, 'rag_system'):
                return {"success": False, "error": "RAG system not available globally"}
            
            rag_sys = app_instance.rag_system
            if not rag_sys or not rag_sys.is_initialized or rag_sys.vector_index is None:
                return {"success": False, "error": "RAG vector database is not fully initialized"}

            # Get embedding synchronously
            try:
                response = requests.post(
                    "http://localhost:11434/api/embeddings",
                    json={
                        "model": rag_sys.embedding_model_name,
                        "prompt": query
                    },
                    timeout=30.0
                )
                if response.status_code != 200:
                    return {"success": False, "error": f"Embedding API error: {response.status_code}"}
                
                embedding = np.array(response.json().get("embedding", []))
                if len(embedding) == 0:
                    return {"success": False, "error": "Empty embedding returned from Ollama"}
            except Exception as e:
                logger.error(f"Error getting embedding: {e}")
                return {"success": False, "error": f"Error getting embedding: {str(e)}"}

            # Search FAISS index synchronously
            try:
                import faiss
                embedding_float32 = np.array([embedding]).astype(np.float32)
                faiss.normalize_L2(embedding_float32)
                
                # Fetch up to 3 similar molecules
                k = kwargs.get("k", 3)
                distances, indices = rag_sys.vector_index.search(embedding_float32, k)
                
                results = []
                for i, idx in enumerate(indices[0]):
                    if idx != -1 and idx < len(rag_sys.molecules_df):
                        mol_data = rag_sys.molecules_df.iloc[idx].to_dict()
                        # Add similarity score
                        mol_data["similarity_score"] = float(distances[0][i])
                        # Filter out NaN values for cleaner JSON
                        import pandas as pd
                        mol_data = {k: v for k, v in mol_data.items() if pd.notna(v)}
                        results.append(mol_data)
                
                if not results:
                    return {"success": True, "data": [], "message": "No similar molecules found in the local database."}
                
                # Format a summary string for the LLM
                summary = "Found the following relevant molecules in the database:\n"
                for i, r in enumerate(results, 1):
                    smiles = r.get("SMILES", "Unknown")
                    score = r.get("similarity_score", 0)
                    summary += f"{i}. SMILES: {smiles} (Similarity: {score:.3f})\n"
                    # Include properties if available
                    for key, val in r.items():
                        if key not in ["SMILES", "similarity_score"]:
                            summary += f"   - {key}: {val}\n"
                
                return {
                    "success": True,
                    "data": results,
                    "summary": summary,
                    "message": f"Found {len(results)} similar molecules."
                }
            except Exception as e:
                logger.error(f"FAISS search error: {e}")
                return {"success": False, "error": f"FAISS search error: {str(e)}"}

        except Exception as e:
            logger.error(f"RAGSearchTool execution error: {e}")
            return {"success": False, "error": str(e)}
