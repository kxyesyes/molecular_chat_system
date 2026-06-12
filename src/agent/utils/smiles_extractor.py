#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SMILES提取和验证工具
"""

import re
from typing import List, Optional, Tuple
from rdkit import Chem
import logging

logger = logging.getLogger(__name__)


class SMILESExtractor:
    """SMILES提取器"""
    
    def __init__(self):
        # SMILES模式 - 匹配常见的SMILES字符
        self.smiles_pattern = re.compile(
            r'[CNOSPFBrClI][\w\d\[\]\(\)@=#\-\+\.\\/:%]*[CNOSPFBrClI\)]|[CNOSPFBrClI]'
        )
    
    def extract_smiles(self, text: str) -> List[str]:
        """从文本中提取SMILES"""
        candidates = []
        
        # 方法1: 括号中的内容
        parentheses = re.findall(r'\(([^)]+)\)', text)
        candidates.extend(parentheses)
        
        # 方法2: 引号中的内容  
        quotes = re.findall(r'["\']([^"\']+)["\']', text)
        candidates.extend(quotes)
        
        # 方法3: 使用SMILES模式匹配
        patterns = self.smiles_pattern.findall(text)
        candidates.extend(patterns)
        
        # 方法4: 空格分隔的token
        tokens = text.split()
        for token in tokens:
            if self._looks_like_smiles(token):
                candidates.append(token)
        
        # 验证并去重
        valid_smiles = []
        seen = set()
        
        for candidate in candidates:
            if candidate and candidate not in seen:
                if self.validate_smiles(candidate):
                    valid_smiles.append(candidate)
                    seen.add(candidate)
        
        return valid_smiles
    
    def _looks_like_smiles(self, text: str) -> bool:
        """快速判断是否可能是SMILES"""
        if len(text) < 2:
            return False
        
        # 包含常见的SMILES字符
        smiles_chars = set('CNOSPFBrClI()[]=#@+-')
        if not any(c in text for c in smiles_chars):
            return False
        
        # 排除明显不是SMILES的词
        exclude_words = {'the', 'and', 'for', 'with', 'calculate', 'predict'}
        if text.lower() in exclude_words:
            return False
        
        return True
    
    def validate_smiles(self, smiles: str) -> bool:
        """验证SMILES是否有效"""
        try:
            mol = Chem.MolFromSmiles(smiles)
            return mol is not None
        except:
            return False
    
    def standardize_smiles(self, smiles: str) -> Optional[str]:
        """标准化SMILES"""
        try:
            mol = Chem.MolFromSmiles(smiles)
            if mol:
                return Chem.MolToSmiles(mol)
        except:
            pass
        return None