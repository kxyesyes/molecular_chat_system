#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基于LLM的分子生成工具 - 使用gmm-llama:latest模型
通过大语言模型生成分子SMILES结构
"""

from typing import Dict, List, Optional, Any
import asyncio
import logging
import re

try:
    from rdkit import Chem
    from rdkit.Chem import Descriptors, Crippen
    RDKIT_AVAILABLE = True
except ImportError:
    RDKIT_AVAILABLE = False

from .base_tool import BaseMolecularTool

logger = logging.getLogger(__name__)


class LLMMolecularGenerator(BaseMolecularTool):
    """
    基于LLM的分子生成工具
    
    使用gmm-llama:latest模型通过自然语言生成分子结构
    """
    
    def __init__(self, llm_model=None):
        """
        初始化LLM分子生成工具
        
        Args:
            llm_model: Ollama模型实例(gmm-llama:latest)
        """
        super().__init__(
            name="llm_molecular_generator",
            description=(
                "Generate molecular SMILES structures using LLM (gmm-llama model). "
                "Can create molecules from natural language descriptions, design drug-like "
                "compounds, and generate molecular variants. 使用大语言模型(gmm-llama)从自然语言"
                "描述生成分子SMILES结构，可以创建类药分子、设计新化合物。"
            )
        )
        
        self.llm = llm_model
        
        # 触发关键词
        self.trigger_words = [
            'generate', 'create', 'design', 'make', 'build', 'synthesize',
            '生成', '创建', '设计', '制作', '构建', '合成',
            'molecule', 'compound', 'drug', 'structure', 'smiles',
            '分子', '化合物', '药物', '结构',
            'new', 'novel', 'innovative',
            '新', '新型', '创新'
        ]
        
        # LLM生成提示词模板
        self.generation_prompt_template = (
            "You are an experienced medicinal chemist working with an 8B llama model that knows SMILES.\n"
            "Task: provide {count} valid, drug-like SMILES that satisfy the request below.\n"
            "Request: {requirements}\n\n"
            "Guidelines:\n"
            "- Return only raw SMILES strings, one per line, no numbering or explanations.\n"
            "- Ensure each SMILES is chemically plausible and Lipinski-friendly unless the request says otherwise.\n"
            "- Prefer diversity while keeping the request in mind.\n"
        )

        self.optimization_prompt_template = (
            "You are an experienced medicinal chemist. Improve the molecule below while keeping its core recognizable.\n"
            "Original SMILES: {smiles}\n"
            "Targets: {objectives}\n\n"
            "Produce {count} alternative SMILES. Each line must contain exactly one valid SMILES with no additional text."
        )

    def _get_model_name(self) -> str:
        """Return the configured LLM model name for result metadata."""
        return getattr(self.llm, "model_name", None) or "gmm-llama"
    
    def should_use(self, query: str) -> bool:
        """判断是否应该使用此工具 - 优化版"""
        query_lower = query.lower()
        
        # 优先级1: 明确的生成请求
        explicit_generation = any(word in query_lower for word in [
            '随机生成', '生成一个', '生成几个', '生成若干', '生成新',
            'generate a', 'generate some', 'create a', 'create new',
            '设计一个', '设计新', '创建一个', '创建新'
        ])
        
        # 优先级2: 检查生成相关的关键词
        has_generation_intent = any(word in query_lower for word in [
            'generate', 'create', 'design', 'make', 'build', 'synthesize',
            '生成', '创建', '设计', '制作', '构建', '合成'
        ])
        
        # 优先级3: 检查分子相关的关键词
        has_molecule_keyword = any(word in query_lower for word in [
            'molecule', 'compound', 'structure', 'smiles', 'drug', '类药',
            '分子', '化合物', '结构', '药物'
        ])
        
        # 优先级4: 检查是否明确要求使用LLM或AI生成
        has_llm_intent = any(word in query_lower for word in [
            'llm', 'ai', 'intelligent', 'smart', 'advanced',
            '智能', '高级', 'gpt', 'llama'
        ])
        
        # 排除：如果是分析现有分子（包含SMILES），则不触发生成
        has_smiles = bool(self.extract_smiles(query))
        if has_smiles and not explicit_generation:
            # 如果查询中有SMILES且不是明确的生成请求，可能是分析任务
            if any(word in query_lower for word in ['分析', '计算', '预测', 'analyze', 'calculate', 'predict']):
                logger.info("检测到SMILES分析任务，不触发分子生成")
                return False
        
        # 决策逻辑
        result = (
            explicit_generation or  # 明确的生成请求
            has_llm_intent or  # 明确要求LLM
            (has_generation_intent and has_molecule_keyword)  # 生成意图+分子关键词
        )
        
        if result:
            logger.info(f"✅ LLMMolecularGenerator触发: 检测到分子生成需求")
            logger.info(f"   - 明确生成: {explicit_generation}")
            logger.info(f"   - 生成意图: {has_generation_intent}")
            logger.info(f"   - 分子关键词: {has_molecule_keyword}")
            logger.info(f"   - LLM意图: {has_llm_intent}")
        
        return result
    
    def execute(self, query: str, temperature: float = 0.7, mol_count: int = None) -> Dict[str, Any]:
        """执行LLM驱动的分子生成 - 带重试机制"""
        result = self._create_base_result(query)
        
        if not self._check_rdkit(result):
            return result
        
        if not self.llm:
            result['message'] = "LLM模型未初始化。请确保gmm-llama模型可用。"
            logger.warning("LLM模型不可用")
            return result
        
        try:
            logger.info(f"使用LLM生成分子: {query[:100]}")
            
            # 分析查询意图
            intent = self._analyze_generation_intent(query)
            intent['temperature'] = temperature  # 注入温度参数
            if mol_count is not None:
                intent['count'] = mol_count  # 注入显式指定的数量
            logger.info(f"生成意图: {intent}")
            
            # 使用重试机制生成足够数量的有效分子
            valid_molecules = self._generate_with_retry(intent)
            
            if not valid_molecules:
                result['message'] = "经过多次尝试，未能生成有效的分子结构。请尝试更具体的描述。"
                return result
            
            # 成功 - 准备结果
            result['success'] = True
            result['data'] = valid_molecules
            result['formatted'] = self._format_llm_results(valid_molecules, intent, query)
            result['message'] = f"LLM成功生成 {len(valid_molecules)} 个分子结构"
            result['reasoning'] = f"使用gmm-llama模型基于您的描述生成了{len(valid_molecules)}个符合要求的分子结构。"
            
        except Exception as e:
            logger.error(f"LLM分子生成失败: {e}", exc_info=True)
            result['message'] = f"生成过程出错: {str(e)}"
        
        return result
    
    def _generate_with_retry(self, intent: Dict[str, Any], max_attempts: int = 5) -> List[Dict[str, Any]]:
        """分子生成 - 简化版，直接信任模型输出"""
        target_count = intent['count']
        valid_molecules = []
        all_generated_smiles = set()  # 避免重复
        
        logger.info(f"🎯 目标生成 {target_count} 个分子")
        
        # 简化重试逻辑，或者直接运行一次
        # 这里保留循环结构以便如果LLM返回空时重试，但不再进行复杂的化学验证
        for attempt in range(1, max_attempts + 1):
            if len(valid_molecules) >= target_count:
                break
            
            # 使用LLM生成分子
            try:
                if intent['type'] == 'optimization' and intent.get('base_smiles'):
                    generated_smiles = self._optimize_with_llm(intent)
                else:
                    generated_smiles = self._generate_with_llm(intent)
                
                if not generated_smiles:
                    continue
                
                # 过滤已生成过的SMILES
                new_smiles = [s for s in generated_smiles if s not in all_generated_smiles]
                all_generated_smiles.update(new_smiles)

                for smiles in new_smiles:
                    if len(valid_molecules) >= target_count:
                        break
                    smiles = smiles.strip().strip("`'\".,;")
                    if not smiles or not self.validate_smiles(smiles):
                        continue
                    canonical = smiles
                    if RDKIT_AVAILABLE:
                        mol = Chem.MolFromSmiles(smiles)
                        if mol is None:
                            continue
                        canonical = Chem.MolToSmiles(mol)
                    valid_molecules.append({
                        "smiles": canonical,
                        "model": self._get_model_name(),
                        "source": "llm",
                    })
            except Exception as attempt_error:
                logger.warning(f"LLM molecule generation attempt failed: {attempt_error}")
                continue

        return valid_molecules[:target_count]
    
    def _analyze_generation_intent(self, query: str) -> Dict[str, Any]:
        """分析生成意图 - 改进版，更准确识别数量"""
        intent = {
            'type': 'description',
            'base_smiles': None,
            'requirements': query,
            'count': 1,  # 默认生成1个分子
            'objectives': []
        }
        
        query_lower = query.lower()
        
        # 检查是否是优化任务
        if any(word in query_lower for word in ['optimize', '优化', 'improve', '改进', 'modify', '修改']):
            intent['type'] = 'optimization'
            # 提取基础SMILES
            smiles_list = self.extract_smiles(query)
            if smiles_list:
                intent['base_smiles'] = smiles_list[0]
        
        # 提取数量要求 - 改进的正则表达式
        # 匹配模式：
        # - "生成3个分子" -> 3
        # - "generate 5 molecules" -> 5
        # - "一个分子" -> 1
        # - "几个分子" -> 3 (默认)
        # - "若干分子" -> 3 (默认)
        
        # 首先检查明确的阿拉伯数字
        count_patterns = [
            r'(\d+)\s*(?:个|种|份|个|structures?|molecules?|compounds?)',  # 数字+量词
            r'(?:生成|创建|设计|generate|create|design)\s*(\d+)',  # 动词+数字
        ]
        
        count_found = False
        for pattern in count_patterns:
            count_match = re.search(pattern, query_lower)
            if count_match:
                intent['count'] = min(int(count_match.group(1)), 10)  # 最多10个
                count_found = True
                break

        # 再检查中文数字，例如“五个类药分子”“生成十种候选结构”“设计两个化合物”
        if not count_found:
            chinese_count_patterns = [
                r'([零〇一二两三四五六七八九十百]+)\s*(?:个|种|份|条)?\s*(?:类药)?(?:分子|化合物|结构|候选)',
                r'(?:生成|创建|设计|给我|提供|produce|make)\s*([零〇一二两三四五六七八九十百]+)\s*(?:个|种|份|条)?',
            ]
            for pattern in chinese_count_patterns:
                count_match = re.search(pattern, query_lower)
                if not count_match:
                    continue
                count_value = self._parse_chinese_count(count_match.group(1))
                if count_value is not None:
                    intent['count'] = min(count_value, 10)  # 最多10个
                    count_found = True
                    break
        
        # 如果没有找到明确数字，检查模糊数量词
        if not count_found:
            if any(word in query_lower for word in ['一个', 'one', 'a molecule', 'a compound', '单个']):
                intent['count'] = 1
            elif any(word in query_lower for word in ['几个', 'some', 'several', '若干', 'a few']):
                intent['count'] = 3
            elif any(word in query_lower for word in ['多个', 'multiple', 'many', '很多']):
                intent['count'] = 5
            # 如果都没有匹配，保持默认值1
        
        # 提取优化目标
        if 'drug' in query_lower or '药' in query_lower or '类药' in query_lower:
            intent['objectives'].append('drug-like properties')
        if 'solub' in query_lower or '溶解' in query_lower:
            intent['objectives'].append('water solubility')
        if 'potency' in query_lower or '活性' in query_lower:
            intent['objectives'].append('biological activity')
        
        return intent

    @staticmethod
    def _parse_chinese_count(text: str) -> Optional[int]:
        """Parse common Chinese integer words used in generation requests."""
        if not text:
            return None

        digits = {
            "零": 0,
            "〇": 0,
            "一": 1,
            "二": 2,
            "两": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
        }
        text = text.strip()
        if text in digits:
            return digits[text]
        if text == "十":
            return 10
        if "百" in text:
            parts = text.split("百", 1)
            hundreds = digits.get(parts[0], 1 if parts[0] == "" else None)
            if hundreds is None:
                return None
            rest = LLMMolecularGenerator._parse_chinese_count(parts[1]) if parts[1] else 0
            if rest is None:
                return None
            return hundreds * 100 + rest
        if "十" in text:
            parts = text.split("十", 1)
            tens = digits.get(parts[0], 1 if parts[0] == "" else None)
            ones = digits.get(parts[1], 0 if parts[1] == "" else None)
            if tens is None or ones is None:
                return None
            return tens * 10 + ones
        return None
    
    def _generate_with_llm(self, intent: Dict[str, Any]) -> List[str]:
        """使用LLM从描述生成分子"""
        try:
            # 构建提示词
            prompt = self.generation_prompt_template.format(
                requirements=intent['requirements'],
                count=intent['count']
            )
            
            logger.info(f"发送LLM请求，生成{intent['count']}个分子")
            
            # 调用LLM (同步方式)
            response = self._call_llm_sync(prompt, temperature=intent.get('temperature', 0.7))
            
            if not response:
                logger.warning("LLM返回空响应")
                return []
            
            # 简单按行提取
            return [line.strip() for line in response.strip().split('\n') if line.strip()]
            
        except Exception as e:
            logger.error(f"LLM生成失败: {e}")
            return []
    
    def _optimize_with_llm(self, intent: Dict[str, Any]) -> List[str]:
        """使用LLM优化现有分子"""
        try:
            base_smiles = intent['base_smiles']
            objectives = ', '.join(intent['objectives']) if intent['objectives'] else 'improve drug-likeness'
            
            prompt = self.optimization_prompt_template.format(
                smiles=base_smiles,
                objectives=objectives,
                count=intent['count']
            )
            
            logger.info(f"使用LLM优化分子: {base_smiles}")
            
            # 调用LLM
            response = self._call_llm_sync(prompt, temperature=intent.get('temperature', 0.7))
            
            if not response:
                return []
            
            # 简单按行提取
            smiles_list = [line.strip() for line in response.strip().split('\n') if line.strip()]
            
            # 确保包含原始分子
            if base_smiles not in smiles_list:
                smiles_list.insert(0, base_smiles)
            
            return smiles_list
            
        except Exception as e:
            logger.error(f"LLM优化失败: {e}")
            return []
    
    def _call_llm_sync(self, prompt: str, temperature: float = 0.7) -> str:
        """同步调用LLM - 兼容异步和同步模型"""
        try:
            # 检查LLM是否可用
            if not self.llm:
                logger.warning("LLM模型未初始化")
                return ""
            
            # 检查是否有 generate 方法
            if not hasattr(self.llm, 'generate'):
                logger.error("LLM 模型没有 generate 方法")
                return ""
            
            # 检查是否是异步方法
            import asyncio
            import inspect
            
            if inspect.iscoroutinefunction(self.llm.generate):
                # 异步方法 - 在新事件循环中运行
                logger.info(f"✅ 检测到异步LLM，使用事件循环调用 (temp={temperature})")
                try:
                    # 尝试获取当前事件循环
                    try:
                        loop = asyncio.get_running_loop()
                        # 如果已经在事件循环中，使用 run_in_executor
                        import concurrent.futures
                        with concurrent.futures.ThreadPoolExecutor() as executor:
                            future = executor.submit(self._run_async_in_new_loop, prompt, temperature)
                            response = future.result(timeout=30)
                            return response
                    except RuntimeError:
                        # 没有运行中的事件循环，创建新的
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        try:
                            response = loop.run_until_complete(
                                asyncio.wait_for(
                                    self.llm.generate(prompt, temperature=temperature, max_tokens=1000),
                                    timeout=30.0
                                )
                            )
                            return response
                        finally:
                            loop.close()
                except Exception as async_error:
                    logger.error(f"异步LLM调用失败: {async_error}")
                    return ""
            else:
                # 同步方法 - 直接调用
                logger.info(f"✅ 使用同步LLM模型生成分子 (temp={temperature})")
                response = self.llm.generate(prompt, temperature=temperature, max_tokens=1000)
                return response
                
        except Exception as e:
            logger.error(f"❌ LLM调用失败: {e}", exc_info=True)
            return ""
    
    def _run_async_in_new_loop(self, prompt: str, temperature: float = 0.7) -> str:
        """在新线程的新事件循环中运行异步调用"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                asyncio.wait_for(
                    self.llm.generate(prompt, temperature=temperature, max_tokens=1000),
                    timeout=30.0
                )
            )
        finally:
            loop.close()
    
    def _format_llm_results(self, molecules: List[Dict[str, Any]], intent: Dict[str, Any], query: str) -> str:
        """格式化LLM生成结果（不在文本中重复数值型分子属性）"""
        output = ["🤖 **LLM分子生成结果** (gmm-llama模型)\n"]

        output.append(f"查询: {query[:100]}")
        output.append(f"生成数量: {len(molecules)}\n")

        if intent.get('base_smiles'):
            output.append(f"基础结构: `{intent['base_smiles']}`\n")

        # 显示每个生成的分子（仅给出结构和定性评价，数值属性交由前端卡片展示）
        for i, mol_data in enumerate(molecules, 1):
            output.append(f"\n**分子 {i}:**")
            output.append(f"- SMILES: `{mol_data['smiles']}`")
            output.append(f"- 生成模型: {mol_data['model']}")

        output.append("\n💡 **建议:**")
        output.append("- 这些分子由AI模型生成，建议进一步进行实验或更高精度计算验证")
        output.append("- 可使用其他工具进行ADMET预测和毒性评估")
        output.append("- 可以根据前端显示的理化性质，对分子进行进一步筛选与优化")

        return "\n".join(output)

