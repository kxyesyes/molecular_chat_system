#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分子对接工具 - 用于蛋白质-配体分子对接预测
"""

from typing import Dict, List, Optional, Any
import logging

from .base_tool import BaseMolecularTool

logger = logging.getLogger(__name__)


class MolecularDocking(BaseMolecularTool):
    """分子对接工具"""

    def __init__(self):
        super().__init__(
            name="molecular_docking",
            description="Perform molecular docking between ligands and protein targets"
        )

        # 触发关键词
        self.trigger_words = [
            'docking', 'dock', 'binding', 'affinity', 'receptor', 'ligand',
            '对接', '结合', '亲和力', '受体', '配体', '分子对接',
            'autodock', 'vina', 'glide', 'binding site', '结合位点',
            'protein-ligand', '蛋白配体', 'target', '靶点'
        ]

        # 常见靶点蛋白质数据库
        self.common_targets = {
            "covid_mpro": {
                "name": "COVID-19 主蛋白酶",
                "pdb_id": "6LU7",
                "description": "SARS-CoV-2主蛋白酶，重要的药物靶点",
                "binding_site": "H41, C145活性位点"
            },
            "egfr": {
                "name": "表皮生长因子受体",
                "pdb_id": "1M17",
                "description": "癌症治疗的重要靶点",
                "binding_site": "ATP结合位点"
            },
            "ace2": {
                "name": "血管紧张素转换酶2",
                "pdb_id": "1R42",
                "description": "高血压和COVID-19相关靶点",
                "binding_site": "锌结合位点"
            }
        }

    def should_use(self, query: str) -> bool:
        """判断是否应该使用此工具"""
        query_lower = query.lower()

        # 检查触发词
        has_trigger = any(word in query_lower for word in self.trigger_words)

        # 检查是否有SMILES或提到了蛋白质/靶点
        smiles_list = self.extract_smiles(query)
        has_smiles = len(smiles_list) > 0

        # 检查是否提到了蛋白质或靶点
        has_target = any(target in query_lower for target in ['protein', 'target', 'receptor', '蛋白', '靶点', '受体'])

        result = has_trigger and (has_smiles or has_target)

        if result:
            logger.info(f"MolecularDocking triggered: found {len(smiles_list)} valid SMILES, trigger={has_trigger}, target={has_target}")

        return result

    def execute(self, query: str | Dict[str, Any]) -> Dict[str, Any]:
        """Run real docking only for structured requests with complete inputs."""
        result = self._create_base_result(query)

        if not isinstance(query, dict):
            result["message"] = (
                "Docking requires receptor and ligand inputs plus docking box "
                "center/size parameters; no binding energy was calculated."
            )
            result["reasoning"] = (
                "A target name and SMILES alone are insufficient for a real "
                "AutoDock Vina run."
            )
            return result

        required = {"receptor_path", "center", "size"}
        missing = sorted(required - set(query))
        if not query.get("ligand_path") and not query.get("smiles"):
            missing.append("ligand_path or smiles")
        if missing:
            result["message"] = (
                "Missing docking inputs: "
                + ", ".join(missing)
                + ". No binding energy was calculated."
            )
            result["reasoning"] = "Docking precondition validation failed."
            return result

        try:
            import asyncio
            from pathlib import Path

            from src.docking.molecular_docking_service import (
                DockingConfig,
                MolecularDockingService,
            )

            receptor_path = Path(str(query["receptor_path"])).expanduser().resolve()
            if not receptor_path.is_file():
                result["message"] = (
                    f"Receptor file does not exist: {receptor_path}. "
                    "No binding energy was calculated."
                )
                return result
            center = tuple(float(value) for value in query["center"])
            size = tuple(float(value) for value in query["size"])
            if len(center) != 3 or len(size) != 3:
                result["message"] = "Docking center and size must each contain 3 values."
                return result
            config = DockingConfig(
                center_x=center[0],
                center_y=center[1],
                center_z=center[2],
                size_x=size[0],
                size_y=size[1],
                size_z=size[2],
                exhaustiveness=int(query.get("exhaustiveness", 8)),
                num_modes=int(query.get("num_modes", 10)),
                energy_range=float(query.get("energy_range", 3.0)),
                manual_center=True,
            )
            service = MolecularDockingService(config=query.get("runtime_config"))
            if not service.verify_environment():
                result["message"] = (
                    "AutoDock Vina docking environment is unavailable. "
                    "No binding energy was calculated."
                )
                result["data"] = {"diagnostics": service.env_diagnostics()}
                return result
            ligand_input = query.get("smiles")
            input_type = "smiles"
            if not ligand_input:
                ligand_path = Path(str(query["ligand_path"])).expanduser().resolve()
                if not ligand_path.is_file():
                    result["message"] = (
                        f"Ligand file does not exist: {ligand_path}. "
                        "No binding energy was calculated."
                    )
                    return result
                ligand_input = str(ligand_path)
                input_type = "file"
            docking_result = asyncio.run(
                service.perform_docking(
                    receptor_file=str(receptor_path),
                    ligand_input=str(ligand_input),
                    config=config,
                    input_type=input_type,
                )
            )
            result["success"] = bool(docking_result.get("success"))
            result["data"] = docking_result
            result["quality"] = {
                "docking_inputs": {
                    "receptor_path": str(receptor_path),
                    "ligand_input": str(ligand_input),
                    "center": list(center),
                    "size": list(size),
                },
                "engine": "AutoDock Vina",
                "real_execution": True,
            }
            result["message"] = (
                "Real AutoDock Vina docking completed."
                if result["success"]
                else docking_result.get("error", "Docking failed.")
            )
            result["formatted"] = (
                f"Docking job: {docking_result.get('job_id')}\n"
                f"Poses: {docking_result.get('total_poses', 0)}"
                if result["success"]
                else ""
            )
            result["reasoning"] = "Results were parsed from an actual Vina output file."
        except Exception as e:
            logger.error(f"Molecular docking failed: {e}")
            result["message"] = f"Docking failed: {str(e)}"
            result["reasoning"] = "The real docking pipeline raised an error."

        return result

    def _identify_target(self, query: str) -> Optional[Dict[str, Any]]:
        """识别目标蛋白质"""
        query_lower = query.lower()

        # 检查是否提到了常见靶点
        for target_key, target_data in self.common_targets.items():
            # 检查靶点名称、PDB ID等
            if (target_key in query_lower or
                target_data["pdb_id"].lower() in query_lower or
                any(keyword in query_lower for keyword in target_data["name"].split())):
                return {
                    "target_id": target_key,
                    "target_name": target_data["name"],
                    "pdb_id": target_data["pdb_id"],
                    "description": target_data["description"],
                    "binding_site": target_data["binding_site"]
                }

        # 检查是否提到了通用关键词
        protein_keywords = ['covid', 'mpro', 'egfr', 'ace2', 'kinase', 'protease']
        for keyword in protein_keywords:
            if keyword in query_lower:
                if 'covid' in keyword or 'mpro' in keyword:
                    return {
                        "target_id": "covid_mpro",
                        "target_name": self.common_targets["covid_mpro"]["name"],
                        "pdb_id": self.common_targets["covid_mpro"]["pdb_id"],
                        "description": self.common_targets["covid_mpro"]["description"],
                        "binding_site": self.common_targets["covid_mpro"]["binding_site"]
                    }

        return None

    def _generate_binding_mode(self, smiles: str) -> str:
        """生成结合模式描述"""
        modes = [
            "深入结合口袋，形成紧密接触",
            "部分暴露于溶剂中，适中结合",
            "表面结合，较弱相互作用",
            "深度埋藏，强疏水相互作用"
        ]
        # 基于SMILES哈希选择模式
        index = hash(smiles) % len(modes)
        return modes[index]

    def _generate_interactions(self, smiles: str) -> List[str]:
        """生成相互作用描述"""
        all_interactions = [
            "氢键: His41-配体羟基",
            "疏水相互作用: Phe140-配体苯环",
            "范德华力: Met49-配体甲基",
            "静电相互作用: Glu166-配体氨基",
            "π-π堆积: His163-配体芳环"
        ]

        # 基于SMILES特征选择相互作用
        interactions = []
        if 'O' in smiles:
            interactions.append(all_interactions[0])
        if 'c' in smiles or 'C6' in smiles:
            interactions.append(all_interactions[1])
        if 'C' in smiles:
            interactions.append(all_interactions[2])
        if 'N' in smiles:
            interactions.append(all_interactions[3])

        return interactions[:3]  # 最多返回3个相互作用

    def _assess_drug_likeness(self, smiles: str) -> str:
        """评估药物相似性"""
        # 简单的药物相似性评估
        if len(smiles) < 10:
            return "分子较小，可能需要优化"
        elif len(smiles) > 50:
            return "分子较大，可能存在膜透过性问题"
        else:
            return "具有良好的药物相似性"

    def _format_docking_results(self, docking_results: Dict[str, Any]) -> str:
        """格式化对接结果"""
        target = docking_results["target"]
        ligands = docking_results["ligands"]

        output = f"""🎯 分子对接结果

目标蛋白质: {target["target_name"]} (PDB: {target["pdb_id"]})
描述: {target["description"]}
结合位点: {target["binding_site"]}

📊 配体对接结果 (按评分排序):
"""

        for i, ligand in enumerate(ligands, 1):
            output += f"""
{i}. 配体 {ligand["ligand_id"]}
   SMILES: `{ligand["smiles"]}`
   对接评分: {ligand["docking_score"]}/10
   结合亲和力: {ligand["binding_affinity"]} kcal/mol
   结合模式: {ligand["binding_mode"]}
   药物相似性: {ligand["drug_likeness"]}

   🔗 主要相互作用:"""

            for interaction in ligand["interactions"]:
                output += f"\n   • {interaction}"

        output += f"""

💡 分析说明:
• 对接评分越高表示结合能力越强
• 结合亲和力负值越大表示结合越稳定
• 相互作用分析有助于理解结合机制
• 建议进一步进行分子动力学模拟验证

⚙️ 方法学: {docking_results["methodology"]}
📈 评分函数: {docking_results["scoring_function"]}"""

        return output

    def _generate_docking_reasoning(self, docking_results: Dict[str, Any]) -> str:
        """生成对接推理"""
        ligands = docking_results["ligands"]
        target_name = docking_results["target"]["target_name"]

        if not ligands:
            return "未能生成有效的对接结果。"

        best_ligand = ligands[0]
        best_score = best_ligand["docking_score"]

        if best_score >= 8.0:
            affinity_desc = "非常强的结合亲和力"
        elif best_score >= 6.0:
            affinity_desc = "较强的结合亲和力"
        elif best_score >= 4.0:
            affinity_desc = "中等的结合亲和力"
        else:
            affinity_desc = "较弱的结合亲和力"

        return f"对 {target_name} 进行了分子对接分析，最佳配体显示{affinity_desc}（评分：{best_score}/10）。共分析了{len(ligands)}个配体分子，提供了详细的结合模式和相互作用分析。"

    def get_supported_targets(self) -> Dict[str, Dict[str, str]]:
        """获取支持的靶点列表"""
        return self.common_targets
