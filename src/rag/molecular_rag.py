#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分子RAG检索系统
基于FAISS和LangChain的向量检索
"""

import os
import logging
import pandas as pd
import numpy as np
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
from dataclasses import dataclass
import re

# 设置日志
logging.basicConfig(level=logging.INFO, encoding='utf-8')
logger = logging.getLogger(__name__)


@dataclass
class MolecularProperty:
    """分子属性数据类"""
    smiles: str
    qed: float
    logp: float
    molwt: float
    hba: int
    hbd: int
    sas: float
    tpsa: float
    num_rot_bonds: int
    
    def to_dict(self) -> dict:
        return {
            'SMILES': self.smiles,
            'qed': self.qed,
            'logp': self.logp,
            'molwt': self.molwt,
            'HBA': self.hba,
            'HBD': self.hbd,
            'SAS': self.sas,
            'TPSA': self.tpsa,
            'NumRotBonds': self.num_rot_bonds
        }


class OllamaEmbeddings:
    """使用Ollama的嵌入模型"""
    
    def __init__(self, model: str = "nomic-embed-text:latest", base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = base_url
        self._embedding_dim = None
    
    def embed_text(self, text: str) -> List[float]:
        """获取文本嵌入"""
        try:
            import httpx
            response = httpx.post(
                f"{self.base_url}/api/embeddings",
                json={"model": self.model, "prompt": text},
                timeout=30.0
            )
            
            if response.status_code == 200:
                embedding = response.json()["embedding"]
                if not self._embedding_dim:
                    self._embedding_dim = len(embedding)
                return embedding
            else:
                logger.error(f"Embedding API error: {response.status_code}")
                return self._get_default_embedding()
                
        except Exception as e:
            logger.error(f"Failed to get embedding: {e}")
            return self._get_default_embedding()
    
    def _get_default_embedding(self) -> List[float]:
        """返回默认嵌入向量"""
        dim = self._embedding_dim or 768
        return [0.0] * dim
    
    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """批量嵌入"""
        return [self.embed_text(text) for text in texts]


class SimpleFAISS:
    """简单的FAISS向量存储"""
    
    def __init__(self, embedding_dim: int = 768):
        self.embedding_dim = embedding_dim
        self.vectors = []
        self.documents = []
        self.index = None
        
        try:
            import faiss
            self.faiss = faiss
            self.index = faiss.IndexFlatL2(embedding_dim)
            self.use_faiss = True
        except ImportError:
            logger.warning("FAISS not available, using numpy similarity search")
            self.use_faiss = False
    
    def add_vectors(self, vectors: List[List[float]], documents: List[Dict]):
        """添加向量和文档"""
        self.vectors.extend(vectors)
        self.documents.extend(documents)
        
        if self.use_faiss and self.index:
            # 添加到FAISS索引
            vectors_array = np.array(vectors, dtype=np.float32)
            self.index.add(vectors_array)
    
    def search(self, query_vector: List[float], k: int = 2) -> List[Tuple[Dict, float]]:
        """搜索最相似的向量"""
        if not self.vectors:
            return []
        
        if self.use_faiss and self.index and self.index.ntotal > 0:
            # 使用FAISS搜索
            query_array = np.array([query_vector], dtype=np.float32)
            distances, indices = self.index.search(query_array, min(k, len(self.documents)))
            
            results = []
            for i, idx in enumerate(indices[0]):
                if idx >= 0 and idx < len(self.documents):
                    results.append((self.documents[idx], float(distances[0][i])))
            return results
        else:
            # 使用numpy计算相似度
            query_array = np.array(query_vector)
            similarities = []
            
            for i, vec in enumerate(self.vectors):
                vec_array = np.array(vec)
                # 计算余弦相似度
                similarity = np.dot(query_array, vec_array) / (np.linalg.norm(query_array) * np.linalg.norm(vec_array) + 1e-8)
                similarities.append((self.documents[i], 1 - similarity))  # 转换为距离
            
            # 排序并返回top k
            similarities.sort(key=lambda x: x[1])
            return similarities[:k]
    
    def save(self, path: str):
        """保存索引"""
        os.makedirs(path, exist_ok=True)
        
        # 保存文档
        import pickle
        with open(os.path.join(path, "documents.pkl"), "wb") as f:
            pickle.dump(self.documents, f)
        
        # 保存向量
        np.save(os.path.join(path, "vectors.npy"), np.array(self.vectors))
        
        # 保存FAISS索引
        if self.use_faiss and self.index:
            self.faiss.write_index(self.index, os.path.join(path, "index.faiss"))
    
    def load(self, path: str) -> bool:
        """加载索引"""
        try:
            import pickle
            
            # 加载文档
            with open(os.path.join(path, "documents.pkl"), "rb") as f:
                self.documents = pickle.load(f)
            
            # 加载向量
            self.vectors = np.load(os.path.join(path, "vectors.npy")).tolist()
            
            # 加载FAISS索引
            if self.use_faiss:
                index_path = os.path.join(path, "index.faiss")
                if os.path.exists(index_path):
                    self.index = self.faiss.read_index(index_path)
            
            return True
        except Exception as e:
            logger.error(f"Failed to load index: {e}")
            return False


class MolecularRAG:
    """分子RAG检索系统"""
    
    def __init__(self, csv_path: Optional[str] = None, 
                 embedding_model: str = "nomic-embed-text:latest",
                 ollama_base_url: str = "http://localhost:11434",
                 vector_store_path: str = "data/molecular_faiss_index"):
        """
        初始化RAG系统
        
        Args:
            csv_path: 分子数据CSV文件路径
            embedding_model: 嵌入模型名称
            ollama_base_url: Ollama服务地址
            vector_store_path: 向量存储路径
        """
        self.embeddings = OllamaEmbeddings(model=embedding_model, base_url=ollama_base_url)
        self.vector_store = SimpleFAISS(embedding_dim=768)
        self.vector_store_path = vector_store_path
        self.molecules_df = None
        self.documents = []
        
        if csv_path and os.path.exists(csv_path):
            self.load_molecules(csv_path)
        elif os.path.exists(vector_store_path):
            self.load_index()
    
    def load_molecules(self, csv_path: str):
        """加载分子数据"""
        try:
            # 读取CSV
            self.molecules_df = pd.read_csv(csv_path)
            logger.info(f"Loaded {len(self.molecules_df)} molecules")
            
            # 检查是否已有索引
            if os.path.exists(self.vector_store_path) and self.vector_store.load(self.vector_store_path):
                logger.info(f"Loaded existing vector index: {self.vector_store_path}")
            else:
                # 创建新索引
                self._create_vector_index()
                
        except Exception as e:
            logger.error(f"Failed to load molecular data: {e}")
            raise
    
    def _create_vector_index(self):
        """创建向量索引"""
        if self.molecules_df is None or len(self.molecules_df) == 0:
            return
        
        logger.info("Creating vector index...")
        
        # 准备文本和文档
        texts = []
        documents = []
        
        for idx, row in self.molecules_df.iterrows():
            # 创建文本表示
            text = self._create_molecule_text(row)
            texts.append(text)
            
            # 创建文档
            doc = {
                'index': idx,
                'smiles': row.get('SMILES', ''),
                'qed': float(row.get('qed', 0)),
                'logp': float(row.get('logp', 0)),
                'molwt': float(row.get('molwt', 0)),
                'hba': int(row.get('HBA', 0)),
                'hbd': int(row.get('HBD', 0)),
                'tpsa': float(row.get('TPSA', 0)),
                'sas': float(row.get('SAS', 0)),
                'num_rot_bonds': int(row.get('NumRotBonds', 0))
            }
            documents.append(doc)
        
        # 获取嵌入向量
        logger.info("Getting embeddings...")
        embeddings = self.embeddings.embed_batch(texts)
        
        # 添加到向量存储
        self.vector_store.add_vectors(embeddings, documents)
        self.documents = documents
        
        # 保存索引
        self.vector_store.save(self.vector_store_path)
        logger.info("Vector index created and saved")
    
    def _create_molecule_text(self, row: pd.Series) -> str:
        """创建分子的文本表示"""
        parts = [
            f"SMILES: {row.get('SMILES', '')}",
            f"QED: {row.get('qed', 0):.3f}",
            f"LogP: {row.get('logp', 0):.3f}",
            f"MW: {row.get('molwt', 0):.1f}",
            f"HBA: {row.get('HBA', 0)}",
            f"HBD: {row.get('HBD', 0)}",
            f"TPSA: {row.get('TPSA', 0):.1f}"
        ]
        return " | ".join(parts)
    
    def load_index(self) -> bool:
        """加载已有索引"""
        if self.vector_store.load(self.vector_store_path):
            self.documents = self.vector_store.documents
            logger.info("Loaded existing vector index")
            return True
        return False
    
    def parse_user_query(self, query: str) -> Tuple[Dict[str, Tuple[str, float]], Optional[str]]:
        """解析用户查询"""
        conditions = {}
        smiles = None

        # 属性列表
        properties = {
            'qed': float,
            'logp': float,
            'molwt': float,
            'mw': float,  # 别名
            'weight': float,  # 别名
            'hba': int,
            'hbd': int,
            'tpsa': float,
            'sas': float,
            'numrotbonds': int,
            'rotbonds': int  # 别名
        }

        # 清理查询并转为小写
        query_lower = query.lower()

        # 解析条件
        for prop, dtype in properties.items():
            # 匹配不同的操作符和中英文表达
            patterns = [
                rf"{prop}\s*([><=]+)\s*([\d.]+)",
                rf"{prop}\s*大于\s*([\d.]+)",
                rf"{prop}\s*小于\s*([\d.]+)",
                rf"{prop}\s*等于\s*([\d.]+)",
                rf"分子量\s*[<>]=?\s*([\d.]+)" if prop in ['molwt', 'mw', 'weight'] else None,
                rf"qed\s*值?\s*[>>=]\s*([\d.]+)" if prop == 'qed' else None
            ]

            # 过滤掉None项
            patterns = [p for p in patterns if p is not None]

            for pattern in patterns:
                matches = re.findall(pattern, query_lower)
                if matches:
                    if len(matches[0]) == 2:  # 有操作符
                        op, value = matches[0]
                    else:  # 只有数值，根据中文推断操作符
                        value = matches[0]
                        if "大于" in query_lower:
                            op = ">"
                        elif "小于" in query_lower:
                            op = "<"
                        else:
                            op = ">"

                    try:
                        value = dtype(value)
                        # 标准化属性名
                        if prop in ['mw', 'weight']:
                            prop = 'molwt'
                        elif prop == 'rotbonds':
                            prop = 'numrotbonds'
                        conditions[prop] = (op, value)
                        break
                    except ValueError:
                        continue

        # 检查是否有SMILES
        if 'c' in query or 'C' in query:
            # 简单的SMILES模式匹配
            smiles_pattern = r'[CNOSPFBrClI][\w\d\[\]\(\)@=#\-\+\.\\/]*[CNOSPFBrClI\)]'
            matches = re.findall(smiles_pattern, query)
            if matches:
                smiles = matches[0]

        return conditions, smiles
    
    def search_by_properties(self, conditions: Dict[str, Tuple[str, float]], k: int = 2) -> List[MolecularProperty]:
        """根据属性条件检索分子"""
        if self.molecules_df is None or len(self.molecules_df) == 0:
            return []
        
        # 应用过滤条件
        filtered_df = self.molecules_df.copy()
        
        for prop, (op, value) in conditions.items():
            # 映射属性名到DataFrame列名
            col_map = {
                'qed': 'qed',
                'logp': 'logp',
                'molwt': 'molwt',
                'hba': 'HBA',
                'hbd': 'HBD',
                'tpsa': 'TPSA',
                'sas': 'SAS',
                'numrotbonds': 'NumRotBonds'
            }
            
            col = col_map.get(prop, prop)
            if col not in filtered_df.columns:
                continue
            
            # 应用操作符
            if op == '>':
                filtered_df = filtered_df[filtered_df[col] > value]
            elif op == '>=':
                filtered_df = filtered_df[filtered_df[col] >= value]
            elif op == '<':
                filtered_df = filtered_df[filtered_df[col] < value]
            elif op == '<=':
                filtered_df = filtered_df[filtered_df[col] <= value]
            elif op in ['==', '=']:
                filtered_df = filtered_df[filtered_df[col] == value]
        
        # 按QED排序（如果有的话）
        if 'qed' in filtered_df.columns and len(filtered_df) > 0:
            filtered_df = filtered_df.sort_values('qed', ascending=False)
        
        # 返回top k个结果
        results = []
        for _, row in filtered_df.head(k).iterrows():
            mol = MolecularProperty(
                smiles=row.get('SMILES', ''),
                qed=float(row.get('qed', 0)),
                logp=float(row.get('logp', 0)),
                molwt=float(row.get('molwt', 0)),
                hba=int(row.get('HBA', 0)),
                hbd=int(row.get('HBD', 0)),
                sas=float(row.get('SAS', 0)),
                tpsa=float(row.get('TPSA', 0)),
                num_rot_bonds=int(row.get('NumRotBonds', 0))
            )
            results.append(mol)
        
        logger.info(f"Property search found {len(results)} molecules")
        return results
    
    def search_by_similarity(self, query_smiles: str, k: int = 2) -> List[Tuple[MolecularProperty, float]]:
        """根据SMILES相似度检索分子"""
        # 创建查询文本
        query_text = f"SMILES: {query_smiles}"
        
        # 获取查询嵌入
        query_embedding = self.embeddings.embed_text(query_text)
        
        # 搜索相似向量
        results = self.vector_store.search(query_embedding, k)
        
        # 转换为MolecularProperty对象
        molecules = []
        for doc, score in results:
            mol = MolecularProperty(
                smiles=doc['smiles'],
                qed=doc['qed'],
                logp=doc['logp'],
                molwt=doc['molwt'],
                hba=doc['hba'],
                hbd=doc['hbd'],
                sas=doc['sas'],
                tpsa=doc['tpsa'],
                num_rot_bonds=doc['num_rot_bonds']
            )
            molecules.append((mol, score))
        
        logger.info(f"Similarity search found {len(molecules)} molecules")
        return molecules
    
    def format_molecules_for_context(self, molecules: List[MolecularProperty]) -> str:
        """格式化分子信息作为上下文"""
        if not molecules:
            return ""

        context_parts = []
        for i, mol in enumerate(molecules, 1):
            context_parts.append(f"分子 {i}:")
            context_parts.append(f"  SMILES: {mol.smiles}")
            context_parts.append(f"  分子量: {mol.molwt:.1f} Da")
            context_parts.append(f"  QED评分: {mol.qed:.3f} (药物相似性评分)")
            context_parts.append(f"  LogP: {mol.logp:.3f} (脂水分配系数)")
            context_parts.append(f"  氢键受体数: {mol.hba}")
            context_parts.append(f"  氢键供体数: {mol.hbd}")
            context_parts.append(f"  拓扑极性表面积: {mol.tpsa:.1f} Ų")
            context_parts.append(f"  合成可达性评分: {mol.sas:.3f}")
            context_parts.append(f"  可旋转键数量: {mol.num_rot_bonds}")
            context_parts.append("")

        return "\n".join(context_parts)

    def format_molecules_for_display(self, molecules: List[MolecularProperty]) -> Dict[str, Any]:
        """格式化分子信息用于前端展示"""
        if not molecules:
            return {"molecules": [], "summary": "未找到相关分子"}

        formatted_molecules = []
        for i, mol in enumerate(molecules, 1):
            formatted_molecules.append({
                "id": i,
                "smiles": mol.smiles,
                "properties": {
                    "分子量": {"value": mol.molwt, "unit": "Da", "description": "分子的相对分子质量"},
                    "QED评分": {"value": mol.qed, "unit": "", "description": "药物相似性评分，范围0-1，越高越好"},
                    "LogP": {"value": mol.logp, "unit": "", "description": "脂水分配系数，影响药物的吸收和分布"},
                    "氢键受体数": {"value": mol.hba, "unit": "个", "description": "可接受氢键的原子数量"},
                    "氢键供体数": {"value": mol.hbd, "unit": "个", "description": "可提供氢键的基团数量"},
                    "TPSA": {"value": mol.tpsa, "unit": "Ų", "description": "拓扑极性表面积，影响膜透过性"},
                    "合成可达性": {"value": mol.sas, "unit": "", "description": "合成难度评分，1-10，越低越容易合成"},
                    "可旋转键数": {"value": mol.num_rot_bonds, "unit": "个", "description": "分子中可自由旋转的单键数量"}
                },
                "drug_likeness": self._evaluate_drug_likeness(mol),
                "recommendations": self._generate_recommendations(mol)
            })

        return {
            "molecules": formatted_molecules,
            "summary": f"找到 {len(molecules)} 个相关分子",
            "total_count": len(molecules)
        }

    def _evaluate_drug_likeness(self, mol: MolecularProperty) -> Dict[str, Any]:
        """评估分子的药物相似性"""
        lipinski_rules = {
            "MW≤500": mol.molwt <= 500,
            "LogP≤5": mol.logp <= 5,
            "HBD≤5": mol.hbd <= 5,
            "HBA≤10": mol.hba <= 10
        }

        lipinski_violations = sum(1 for rule, passed in lipinski_rules.items() if not passed)

        other_rules = {
            "TPSA≤140": mol.tpsa <= 140,
            "RotBonds≤10": mol.num_rot_bonds <= 10
        }

        return {
            "lipinski_rules": lipinski_rules,
            "lipinski_violations": lipinski_violations,
            "other_rules": other_rules,
            "overall_assessment": self._get_drug_likeness_assessment(lipinski_violations, mol)
        }

    def _get_drug_likeness_assessment(self, violations: int, mol: MolecularProperty) -> str:
        """获取药物相似性总体评估"""
        if violations == 0 and mol.qed > 0.7:
            return "优秀 - 具有很好的药物相似性"
        elif violations <= 1 and mol.qed > 0.5:
            return "良好 - 具有较好的药物相似性"
        elif violations <= 2:
            return "一般 - 需要进一步优化"
        else:
            return "较差 - 药物相似性不佳，需要大幅改进"

    def _generate_recommendations(self, mol: MolecularProperty) -> List[str]:
        """生成改进建议"""
        recommendations = []

        if mol.molwt > 500:
            recommendations.append("分子量偏高，建议去除非必要的基团以降低分子量")
        if mol.logp > 5:
            recommendations.append("脂溶性过高，建议引入极性基团以降低LogP")
        if mol.hbd > 5:
            recommendations.append("氢键供体过多，可能影响膜透过性")
        if mol.hba > 10:
            recommendations.append("氢键受体过多，建议减少极性基团")
        if mol.tpsa > 140:
            recommendations.append("极性表面积过大，可能影响口服吸收")
        if mol.num_rot_bonds > 10:
            recommendations.append("可旋转键过多，可能导致构象熵不利，建议引入环状结构")
        if mol.qed < 0.5:
            recommendations.append("QED评分较低，建议综合优化分子结构")
        if mol.sas > 6:
            recommendations.append("合成难度较高，建议简化分子结构")

        if not recommendations:
            recommendations.append("该分子具有良好的药物相似性特征")

        return recommendations