"""
ChEMBL 数据提取和清洗脚本
从 ChEMBL 数据库提取化合物-靶点-活性数据并清洗
"""

import sqlite3
import pandas as pd
from pathlib import Path
from tqdm import tqdm

if __package__ in {None, ""}:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.reverse_target.config import (
    configure_console_output,
    get_chembl_db_path,
    get_reverse_target_data_dir,
)
from src.reverse_target.data_quality import (
    apply_activity_quality_flags,
    deduplicate_activity_rows,
    quality_filter_for_training,
    training_columns,
)


class ChEMBLDataExtractor:
    def __init__(self, db_path=None):
        default_path = db_path or get_chembl_db_path()
        self.db_path = Path(default_path)
        self.output_dir = get_reverse_target_data_dir()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        if not self.db_path.exists():
            raise FileNotFoundError(f"数据库文件不存在: {self.db_path}")
        
        # 生物体名称映射（标准化为 Human）
        self.organism_map = {
            'Homo sapiens': 'Human',
            'homo sapiens': 'Human',
            'Human': 'Human',
            'HUMAN': 'Human',
        }
    
    def extract_raw_data(self):
        """从 ChEMBL 数据库提取原始数据"""
        print("连接数据库...")
        conn = sqlite3.connect(self.db_path)
        
        # SQL 查询：提取化合物-靶点-活性数据
        query = """
        SELECT DISTINCT
            md.chembl_id AS molecule_chembl_id,
            cs.canonical_smiles,
            td.pref_name AS target_name,
            act.standard_type,
            act.standard_value,
            act.standard_units,
            act.standard_relation,
            act.pchembl_value,
            td.organism,
            td.chembl_id AS target_chembl_id,
            td.tax_id AS taxon_id,
            act.assay_id,
            a.confidence_score AS assay_confidence_score,
            d.chembl_id AS document_chembl_id
        FROM
            activities act
        INNER JOIN
            molecule_dictionary md ON act.molregno = md.molregno
        INNER JOIN
            compound_structures cs ON md.molregno = cs.molregno
        INNER JOIN
            assays a ON act.assay_id = a.assay_id
        INNER JOIN
            target_dictionary td ON a.tid = td.tid
        LEFT JOIN
            docs d ON a.doc_id = d.doc_id
        WHERE
            act.standard_type IS NOT NULL
            AND act.standard_value IS NOT NULL
            AND cs.canonical_smiles IS NOT NULL
            AND td.pref_name IS NOT NULL
            AND act.standard_type IN ('IC50', 'EC50', 'Ki', 'Kd')
            AND td.organism IS NOT NULL
            AND td.target_type = 'SINGLE PROTEIN'
        LIMIT 1000000
        """
        
        print("正在提取数据（可能需要几分钟）...")
        df = pd.read_sql_query(query, conn)
        conn.close()
        
        print(f"提取到 {len(df)} 条原始数据")
        
        # 保存原始数据
        raw_output = self.output_dir / "chembl_raw_data.tsv"
        df.to_csv(raw_output, sep='\t', index=False)
        print(f"原始数据已保存: {raw_output}")
        
        return df
    
    def clean_data(self, df):
        """清洗数据"""
        print("\n开始数据清洗...")
        
        initial_count = len(df)
        print(f"初始数据量: {initial_count}")
        
        # 1. 移除空值
        df = df.dropna(subset=['molecule_chembl_id', 'canonical_smiles', 'target_name',
                                'standard_type', 'standard_value', 'organism'])
        print(f"移除空值后: {len(df)} 条 (-{initial_count - len(df)})")
        
        # 2. 只移除完全相同的观测；不同 assay、物种或文献不能合并
        df = deduplicate_activity_rows(df)
        print(f"移除重复后: {len(df)} 条")
        
        # 3. 标准化生物体名称
        df['organism'] = df['organism'].map(self.organism_map).fillna(df['organism'])
        
        # 4. 过滤只保留人类数据（可选）
        # df = df[df['organism'] == 'Human']
        # print(f"过滤人类数据后: {len(df)} 条")
        
        # 5. 转换 standard_value 为数值型
        df['standard_value'] = pd.to_numeric(df['standard_value'], errors='coerce')
        df = df.dropna(subset=['standard_value'])
        
        # 6. 过滤异常值（通常活性值在 0.001 到 100000000 nM 范围）
        df = df[(df['standard_value'] > 0) & (df['standard_value'] < 1e8)]
        print(f"过滤异常值后: {len(df)} 条")
        
        # 7. 单位、关系和 assay provenance 由统一质量门处理；未知单位不再猜作 nM
        df = apply_activity_quality_flags(df)
        
        print(f"最终数据量: {len(df)} 条")
        
        return df
    
    def format_training_data(self, df):
        """格式化为训练数据格式"""
        print("\n格式化训练数据...")
        
        # 训练索引只接收质量门通过的记录；完整清洗结果另存为审计表
        audit_path = self.output_dir / "chembl_quality_audit.tsv"
        df.to_csv(audit_path, sep='\t', index=False)
        training_source = quality_filter_for_training(df)
        training_df = training_source[training_columns(training_source)].copy()
        
        # 重命名列以匹配要求的格式
        training_df = training_df.rename(columns={'standard_value_nm': 'standard_value'})
        
        # 保存训练数据
        output_path = self.output_dir / "chembl_training_data.tsv"
        training_df.to_csv(output_path, sep='\t', index=False)
        print(f"训练数据已保存: {output_path}")
        
        # 显示数据统计
        print("\n数据统计:")
        print(f"- 唯一化合物数: {training_df['molecule_chembl_id'].nunique()}")
        print(f"- 唯一靶点数: {training_df['target_name'].nunique()}")
        print(f"- 活性类型分布:")
        print(training_df['standard_type'].value_counts())
        print(f"- 生物体分布:")
        print(training_df['organism'].value_counts())
        
        # 保存示例数据
        sample_output = self.output_dir / "chembl_sample_data.tsv"
        training_df.head(1000).to_csv(sample_output, sep='\t', index=False)
        print(f"\n示例数据已保存: {sample_output}")
        
        return training_df


def main():
    """主函数"""
    configure_console_output()
    print("=" * 60)
    print("ChEMBL 数据提取和清洗工具")
    print("=" * 60)
    
    try:
        extractor = ChEMBLDataExtractor()
        
        # 提取原始数据
        print("\n步骤 1/3: 提取原始数据")
        raw_df = extractor.extract_raw_data()
        
        # 清洗数据
        print("\n步骤 2/3: 清洗数据")
        clean_df = extractor.clean_data(raw_df)
        
        # 格式化训练数据
        print("\n步骤 3/3: 格式化训练数据")
        training_df = extractor.format_training_data(clean_df)
        
        print("\n" + "=" * 60)
        print("✓ 数据提取和清洗完成")
        print(f"✓ 最终数据量: {len(training_df)} 条")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
