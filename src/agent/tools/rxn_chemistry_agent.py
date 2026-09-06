#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基于RXN for Chemistry的反应预测、逆合成分析和文献数据集成agent
"""

import os
import json
import logging
import requests
from typing import Dict, List, Optional, Any, Union
from .base_tool import BaseMolecularTool

logger = logging.getLogger(__name__)


class RXNChemistryAgent(BaseMolecularTool):
    """RXN for Chemistry综合agent - 支持反应预测、逆合成分析和文献数据集成"""

    def __init__(self, api_key: str = None):
        super().__init__(
            name="rxn_chemistry_agent",
            description="Advanced chemical reaction prediction, retrosynthesis analysis, and synthesis route planning using IBM RXN for Chemistry. Specializes in: 1) Predicting reaction products from reactants, 2) Retrosynthetic analysis for complex molecules, 3) Synthesis pathway optimization, 4) Chemical literature integration"
        )

        # RXN for Chemistry API配置
        self.api_key = (api_key or os.environ.get("RXN_API_KEY", "")).strip()
        self.base_url = "https://rxn.res.ibm.com/rxn/api/api/v1"
        self.headers = {
            "Authorization": f"APIKey {self.api_key}",
            "Content-Type": "application/json"
        }

        # 触发关键词
        self.trigger_words = [
            # 反应预测相关
            'reaction', 'predict', 'product', 'synthesis', 'react',
            '反应', '预测', '产物', '合成', '生成', '反应预测',

            # 逆合成相关 - 增强覆盖
            'retrosynthesis', 'retro', 'synthetic route', 'synthesis pathway', 'synthesis route',
            '逆合成', '合成路线', '合成路径', '逆向合成', '逆合成路线', '逆合成分析',
            '合成方法', '合成途径', '制备方法', '制备路线',

            # 文献和数据相关
            'literature', 'reference', 'paper', 'publication', 'data',
            '文献', '参考文献', '论文', '数据', '资料'
        ]

    def should_use(self, query: str) -> bool:
        """判断是否应该使用此工具"""
        query_lower = query.lower()

        # 检查触发词
        has_trigger = any(word in query_lower for word in self.trigger_words)

        # 提取并验证SMILES
        smiles_list = self.extract_smiles(query)
        has_valid_smiles = len(smiles_list) > 0

        result = has_trigger and has_valid_smiles

        if result:
            logger.info(f"RXNChemistryAgent triggered: found {len(smiles_list)} valid SMILES with trigger words: {[word for word in self.trigger_words if word in query_lower]}")
        elif has_valid_smiles and not has_trigger:
            logger.info(f"RXNChemistryAgent NOT triggered: found SMILES but no trigger words. Query: {query[:50]}...")
        elif has_trigger and not has_valid_smiles:
            logger.info(f"RXNChemistryAgent NOT triggered: found trigger words but no valid SMILES. Query: {query[:50]}...")

        return result

    def execute(self, query: str) -> Dict[str, Any]:
        """执行RXN Chemistry任务"""
        result = self._create_base_result(query)

        try:
            # 提取SMILES
            smiles_list = self.extract_smiles(query)

            if not smiles_list:
                result['message'] = "在查询中未找到有效的SMILES分子结构。"
                result['reasoning'] = "我在输入中搜索了SMILES模式，但无法识别任何有效的分子结构。"
                return result

            # 判断任务类型
            task_type = self._determine_task_type(query)

            if task_type == "reaction_prediction":
                return self._handle_reaction_prediction(smiles_list, query, result)
            elif task_type == "retrosynthesis":
                return self._handle_retrosynthesis(smiles_list[0], query, result)
            elif task_type == "literature_search":
                return self._handle_literature_search(smiles_list, query, result)
            else:
                # 默认进行反应预测
                return self._handle_reaction_prediction(smiles_list, query, result)

        except Exception as e:
            logger.error(f"RXN Chemistry execution failed: {e}")
            result['message'] = f"RXN Chemistry服务调用失败: {str(e)}"
            result['reasoning'] = "在调用RXN for Chemistry服务时发生了错误。"

        return result

    def _determine_task_type(self, query: str) -> str:
        """确定任务类型"""
        query_lower = query.lower()

        retro_keywords = [
            'retrosynthesis', 'retro', 'synthetic route', 'synthesis pathway', 'synthesis route',
            '逆合成', '合成路线', '合成路径', '逆向合成', '逆合成路线', '逆合成分析',
            '合成方法', '合成途径', '制备方法', '制备路线'
        ]

        literature_keywords = [
            'literature', 'reference', 'paper', 'publication', 'data',
            '文献', '参考文献', '论文', '数据', '资料'
        ]

        if any(word in query_lower for word in retro_keywords):
            return "retrosynthesis"
        elif any(word in query_lower for word in literature_keywords):
            return "literature_search"
        else:
            return "reaction_prediction"

    def _handle_reaction_prediction(self, smiles_list: List[str], query: str, result: Dict) -> Dict[str, Any]:
        """处理反应预测"""
        try:
            # 构建反应物SMILES
            if len(smiles_list) == 1:
                reactants = smiles_list[0]
            else:
                reactants = ".".join(smiles_list)

            logger.info(f"Predicting reaction for: {reactants}")

            # 调用RXN API进行反应预测
            prediction_data = self._call_reaction_prediction_api(reactants)

            if not prediction_data:
                result['message'] = "反应预测失败，请稍后重试。"
                result['reasoning'] = "RXN for Chemistry API调用失败或返回了无效数据。"
                return result

            # 成功处理结果
            result['success'] = True
            result['data'] = {
                'reactants': reactants,
                'predictions': prediction_data,
                'task_type': 'reaction_prediction'
            }
            result['formatted'] = self._format_reaction_prediction(reactants, prediction_data)
            result['reasoning'] = f"使用IBM RXN for Chemistry成功预测了反应物 {reactants} 的可能反应产物。"
            result['message'] = f"成功预测了 {len(prediction_data.get('predictions', []))} 个可能的反应结果"

        except Exception as e:
            logger.error(f"Reaction prediction failed: {e}")
            result['message'] = f"反应预测过程中发生错误: {str(e)}"
            result['reasoning'] = "在处理反应预测时遇到了意外错误。"

        return result

    def _handle_retrosynthesis(self, target_smiles: str, query: str, result: Dict) -> Dict[str, Any]:
        """处理逆合成分析"""
        try:
            logger.info(f"Performing retrosynthesis for: {target_smiles}")

            # 调用RXN API进行逆合成分析
            retro_data = self._call_retrosynthesis_api(target_smiles)

            if not retro_data:
                result['message'] = "逆合成分析失败，请稍后重试。"
                result['reasoning'] = "RXN for Chemistry API调用失败或返回了无效数据。"
                return result

            # 成功处理结果
            result['success'] = True
            result['data'] = {
                'target': target_smiles,
                'retrosynthesis': retro_data,
                'task_type': 'retrosynthesis'
            }
            result['formatted'] = self._format_retrosynthesis_result(target_smiles, retro_data)
            result['reasoning'] = f"使用IBM RXN for Chemistry成功分析了目标分子 {target_smiles} 的逆合成路线。"
            result['message'] = f"成功生成了 {len(retro_data.get('routes', []))} 个合成路径建议"

        except Exception as e:
            logger.error(f"Retrosynthesis failed: {e}")
            result['message'] = f"逆合成分析过程中发生错误: {str(e)}"
            result['reasoning'] = "在处理逆合成分析时遇到了意外错误。"

        return result

    def _handle_literature_search(self, smiles_list: List[str], query: str, result: Dict) -> Dict[str, Any]:
        """处理文献和数据搜索"""
        try:
            # 使用第一个SMILES进行文献搜索
            target_smiles = smiles_list[0]
            logger.info(f"Searching literature for: {target_smiles}")

            # 调用文献搜索API
            literature_data = self._search_literature_api(target_smiles)

            if not literature_data:
                result['message'] = "文献搜索失败，未找到相关数据。"
                result['reasoning'] = "在RXN for Chemistry数据库中未找到相关的文献信息。"
                return result

            # 成功处理结果
            result['success'] = True
            result['data'] = {
                'query_molecule': target_smiles,
                'literature': literature_data,
                'task_type': 'literature_search'
            }
            result['formatted'] = self._format_literature_result(target_smiles, literature_data)
            result['reasoning'] = f"成功在RXN for Chemistry数据库中找到了与分子 {target_smiles} 相关的文献信息。"
            result['message'] = f"找到了 {len(literature_data.get('references', []))} 个相关文献引用"

        except Exception as e:
            logger.error(f"Literature search failed: {e}")
            result['message'] = f"文献搜索过程中发生错误: {str(e)}"
            result['reasoning'] = "在搜索文献数据时遇到了意外错误。"

        return result

    def _call_reaction_prediction_api(self, reactants: str) -> Optional[Dict[str, Any]]:
        """调用RXN for Chemistry反应预测API"""
        if not self.api_key:
            logger.error("RXN_API_KEY is not configured")
            return None
        try:
            url = f"{self.base_url}/reaction-prediction"
            payload = {
                "reactants": reactants,
                "ai_model": "2020-07-01"  # 使用最新的AI模型
            }

            logger.info(f"Calling RXN API: {url}")
            logger.debug(f"Request payload: {payload}")

            response = requests.post(url, headers=self.headers, json=payload, timeout=30)

            logger.info(f"RXN API response status: {response.status_code}")

            if response.status_code == 200:
                data = response.json()
                logger.info("RXN API call successful")
                return data.get("payload", {})
            elif response.status_code == 401:
                logger.error("RXN API authentication failed - API key may be invalid or expired")
                return None
            elif response.status_code == 403:
                logger.error("RXN API access forbidden - insufficient permissions")
                return None
            elif response.status_code == 500:
                logger.error("RXN API server error - service may be temporarily unavailable")
                logger.debug(f"Server error details: {response.text[:500]}")
                return None
            else:
                logger.error(f"RXN API unexpected status code: {response.status_code}")
                logger.debug(f"Response text: {response.text[:200]}")
                return None

        except requests.exceptions.ConnectTimeout:
            logger.error("RXN API connection timeout - service may be unavailable")
            return None
        except requests.exceptions.ReadTimeout:
            logger.error("RXN API read timeout - service response too slow")
            return None
        except requests.exceptions.ConnectionError as e:
            logger.error(f"RXN API connection error: {e}")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"RXN API request failed: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error in reaction prediction API call: {e}")
            return None

    def _call_retrosynthesis_api(self, target: str) -> Optional[Dict[str, Any]]:
        """调用RXN for Chemistry逆合成API"""
        if not self.api_key:
            logger.error("RXN_API_KEY is not configured")
            return None
        try:
            url = f"{self.base_url}/retrosynthesis"
            payload = {
                "target": target,
                "ai_model": "2020-07-01",
                "max_steps": 5  # 最大步数
            }

            response = requests.post(url, headers=self.headers, json=payload, timeout=60)

            if response.status_code == 200:
                data = response.json()
                return data.get("payload", {})
            else:
                logger.error(f"RXN API error: {response.status_code}, {response.text}")
                return None

        except requests.exceptions.RequestException as e:
            logger.error(f"RXN API request failed: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error in retrosynthesis API call: {e}")
            return None

    def _search_literature_api(self, molecule: str) -> Optional[Dict[str, Any]]:
        """Literature search is unavailable until a real provider is configured."""
        logger.warning(
            "RXN literature search has no configured real endpoint; "
            "no citations were generated for %s",
            molecule,
        )
        return None

    def _format_reaction_prediction(self, reactants: str, prediction_data: Dict) -> str:
        """格式化反应预测结果"""
        predictions = prediction_data.get("predictions", [])

        output = f"""🧪 RXN for Chemistry反应预测结果

反应物: `{reactants}`

📊 预测的可能产物 ({len(predictions)} 个):"""

        for i, prediction in enumerate(predictions, 1):
            confidence = prediction.get("confidence", 0.0)
            products = prediction.get("products", "")
            output += f"\n{i}. `{products}` (置信度: {confidence:.2%})"

        output += f"""

✅ 预测模型: IBM RXN for Chemistry (2020-07-01)
🔬 基于: 大规模化学反应数据库和深度学习模型

💡 说明: 预测结果按置信度排序，置信度越高表示反应越可能发生。"""

        return output

    def _format_retrosynthesis_result(self, target: str, retro_data: Dict) -> str:
        """格式化逆合成结果"""
        routes = retro_data.get("routes", [])

        if not routes:
            return f"""🔬 逆合成分析结果

目标分子: `{target}`

❌ 未找到可行的合成路线。请检查分子结构或稍后重试。

💡 说明: 逆合成分析需要复杂的化学知识和数据库支持。"""

        output = f"""🔬 逆合成分析结果

目标分子: `{target}`

🛤️ 建议的合成路线 ({len(routes)} 条):"""

        for i, route in enumerate(routes, 1):
            steps = route.get("steps", [])
            confidence = route.get("confidence", 0.0)

            output += f"\n\n**路线 {i}** (置信度: {confidence:.1%})"

            if not steps:
                output += "\n  暂无详细步骤信息"
            else:
                for j, step in enumerate(steps, 1):
                    reactants = step.get("reactants", "未知反应物")
                    products = step.get("products", "未知产物")
                    reaction_type = step.get("reaction_type", "")
                    description = step.get("description", "")

                    # 简化产物显示
                    if products == target:
                        products_display = "目标分子"
                    elif len(products) > 50:
                        products_display = "中间体"
                    else:
                        products_display = products

                    output += f"\n  步骤 {j}: {reactants} → {products_display}"

                    if reaction_type:
                        output += f" ({reaction_type})"

                    if description:
                        output += f"\n    说明: {description}"

        output += f"""

✅ 分析引擎: IBM RXN for Chemistry
🎯 优化目标: 反应可行性、成本效益、步骤数量
🔬 数据源: 大规模化学反应数据库和机器学习模型

💡 说明: 合成路线按置信度排序，考虑了反应的可行性和文献报道的先例。建议进一步验证反应条件和收率。"""

        return output

    def _format_literature_result(self, molecule: str, literature_data: Dict) -> str:
        """格式化文献搜索结果"""
        references = literature_data.get("references", [])
        patents = literature_data.get("patents", [])

        output = f"""📚 分子文献数据搜索结果

查询分子: `{molecule}`

📄 相关文献 ({len(references)} 篇):"""

        for i, ref in enumerate(references, 1):
            title = ref.get("title", "")
            authors = ref.get("authors", [])
            journal = ref.get("journal", "")
            year = ref.get("year", "")
            doi = ref.get("doi", "")

            output += f"""
{i}. **{title}**
   作者: {', '.join(authors)}
   期刊: {journal} ({year})
   DOI: {doi}"""

        if patents:
            output += f"\n\n📋 相关专利 ({len(patents)} 项):"

            for i, patent in enumerate(patents, 1):
                title = patent.get("title", "")
                number = patent.get("patent_number", "")
                year = patent.get("year", "")

                output += f"""
{i}. **{title}**
   专利号: {number} ({year})"""

        output += f"""

🔍 数据来源: IBM RXN for Chemistry数据库
📊 检索日期: 最新数据

💡 说明: 以上文献和专利数据来自RXN for Chemistry的综合化学数据库。"""

        return output

    def check_service_availability(self) -> Dict[str, Any]:
        """检查RXN for Chemistry服务是否可用，返回详细状态"""
        status = {
            "available": False,
            "status_code": None,
            "error_message": "",
            "last_check": None,
            "service_info": ""
        }

        try:
            import datetime
            status["last_check"] = datetime.datetime.now().isoformat()

            # 尝试调用一个简单的API端点来检查服务状态
            url = f"{self.base_url}/reaction-prediction"
            payload = {
                "reactants": "CCO",  # 简单的测试分子
                "ai_model": "2020-07-01"
            }

            logger.info("Checking RXN for Chemistry service availability...")

            response = requests.post(url, headers=self.headers, json=payload, timeout=10)
            status["status_code"] = response.status_code

            if response.status_code == 200:
                status["available"] = True
                status["service_info"] = "IBM RXN for Chemistry service is operational"
                logger.info("RXN service is available")
            elif response.status_code == 401:
                status["error_message"] = "Authentication failed - API key may be invalid"
                status["service_info"] = "Service reachable but authentication failed"
            elif response.status_code == 403:
                status["error_message"] = "Access forbidden - insufficient permissions"
                status["service_info"] = "Service reachable but access denied"
            elif response.status_code == 500:
                status["error_message"] = "Server internal error - service temporarily unavailable"
                status["service_info"] = "IBM RXN service is experiencing technical difficulties"
            else:
                status["error_message"] = f"Unexpected status code: {response.status_code}"
                status["service_info"] = "Service status unknown"

        except requests.exceptions.ConnectTimeout:
            status["error_message"] = "Connection timeout - service may be down"
            status["service_info"] = "Unable to connect to IBM RXN service"
        except requests.exceptions.ConnectionError:
            status["error_message"] = "Connection error - service unreachable"
            status["service_info"] = "Network connection to IBM RXN service failed"
        except Exception as e:
            status["error_message"] = f"Service check failed: {str(e)}"
            status["service_info"] = "Unable to determine service status"
            logger.error(f"Service availability check failed: {e}")

        return status
