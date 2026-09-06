"""
使用 ChEMBL Web Services API 提取数据
无需下载完整的 SQLite 数据库
"""

import requests
import pandas as pd
import time
from pathlib import Path
from tqdm import tqdm

from src.reverse_target.config import get_reverse_target_data_dir


class ChEMBLAPIFetcher:
    def __init__(self, output_dir=None):
        self.output_dir = Path(output_dir) if output_dir else get_reverse_target_data_dir()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.base_url = "https://www.ebi.ac.uk/chembl/api/data"
        self.session = requests.Session()
        
    def fetch_activities(self, limit=100000, offset=0):
        """
        从 ChEMBL API 获取活性数据
        """
        print(f"正在从 ChEMBL API 获取数据...")
        print(f"目标: {limit} 条记录")
        
        all_data = []
        batch_size = 1000  # 每批获取 1000 条
        
        with tqdm(total=limit, desc="获取数据") as pbar:
            for offset in range(0, limit, batch_size):
                current_batch = min(batch_size, limit - offset)
                
                url = f"{self.base_url}/activity.json"
                params = {
                    'limit': current_batch,
                    'offset': offset,
                    'standard_type__in': 'IC50,EC50,Ki,Kd',
                    'target_organism': 'Homo sapiens',
                    'assay_type': 'B'  # Binding assay
                }
                
                try:
                    response = self.session.get(url, params=params, timeout=30)
                    response.raise_for_status()
                    
                    data = response.json()
                    activities = data.get('activities', [])
                    
                    for act in activities:
                        try:
                            record = {
                                'molecule_chembl_id': act.get('molecule_chembl_id'),
                                'canonical_smiles': act.get('canonical_smiles'),
                                'target_chembl_id': act.get('target_chembl_id'),
                                'target_pref_name': act.get('target_pref_name'),
                                'standard_type': act.get('standard_type'),
                                'standard_value': act.get('standard_value'),
                                'standard_units': act.get('standard_units'),
                                'pchembl_value': act.get('pchembl_value'),
                                'target_organism': act.get('target_organism')
                            }
                            
                            # 过滤无效数据
                            if (record['molecule_chembl_id'] and 
                                record['canonical_smiles'] and 
                                record['target_pref_name'] and
                                record['standard_value']):
                                all_data.append(record)
                        except:
                            continue
                    
                    pbar.update(len(activities))
                    
                    # 避免请求过快
                    time.sleep(0.1)
                    
                except Exception as e:
                    print(f"\n警告: 批次 {offset} 获取失败: {e}")
                    continue
        
        print(f"\n成功获取 {len(all_data)} 条有效记录")
        return pd.DataFrame(all_data)
    
    def clean_data(self, df):
        """清洗数据"""
        print("\n开始数据清洗...")
        
        initial_count = len(df)
        print(f"初始数据量: {initial_count}")
        
        # 移除空值
        df = df.dropna(subset=['molecule_chembl_id', 'canonical_smiles', 
                                'target_pref_name', 'standard_type', 'standard_value'])
        print(f"移除空值后: {len(df)} 条")
        
        # 移除重复
        df = df.drop_duplicates(subset=['molecule_chembl_id', 'target_pref_name', 'standard_type'])
        print(f"移除重复后: {len(df)} 条")
        
        # 转换数值
        df['standard_value'] = pd.to_numeric(df['standard_value'], errors='coerce')
        df = df.dropna(subset=['standard_value'])
        
        # 过滤异常值
        df = df[(df['standard_value'] > 0) & (df['standard_value'] < 1e8)]
        print(f"过滤异常值后: {len(df)} 条")
        
        # 标准化单位为 nM
        def convert_to_nm(row):
            value = row['standard_value']
            unit = row.get('standard_units', 'nM')
            
            if pd.isna(unit):
                return value
            
            unit = unit.lower().strip()
            
            if unit in ['nm', 'nanomolar']:
                return value
            elif unit in ['um', 'µm', 'micromolar', 'μm']:
                return value * 1000
            elif unit in ['mm', 'millimolar']:
                return value * 1000000
            elif unit in ['m', 'molar']:
                return value * 1000000000
            elif unit in ['pm', 'picomolar']:
                return value / 1000
            else:
                return value
        
        df['standard_value_nm'] = df.apply(convert_to_nm, axis=1)
        
        # 标准化生物体名称
        df['organism'] = df['target_organism'].apply(
            lambda x: 'Human' if x and 'sapiens' in x.lower() else x
        )
        
        print(f"最终数据量: {len(df)} 条")
        return df
    
    def save_training_data(self, df):
        """保存训练数据"""
        print("\n保存训练数据...")
        
        # 选择需要的列
        training_df = df[[
            'molecule_chembl_id',
            'canonical_smiles',
            'target_pref_name',
            'standard_type',
            'standard_value_nm',
            'organism'
        ]].copy()
        
        # 重命名
        training_df.columns = [
            'molecule_chembl_id',
            'canonical_smiles',
            'target_name',
            'standard_type',
            'standard_value',
            'organism'
        ]
        
        # 保存
        output_path = self.output_dir / "chembl_training_data.tsv"
        training_df.to_csv(output_path, sep='\t', index=False)
        print(f"训练数据已保存: {output_path}")
        
        # 统计
        print("\n数据统计:")
        print(f"- 唯一化合物数: {training_df['molecule_chembl_id'].nunique()}")
        print(f"- 唯一靶点数: {training_df['target_name'].nunique()}")
        print(f"- 活性类型分布:")
        print(training_df['standard_type'].value_counts())
        
        # 保存示例
        sample_output = self.output_dir / "chembl_sample_data.tsv"
        training_df.head(100).to_csv(sample_output, sep='\t', index=False)
        print(f"\n示例数据已保存: {sample_output}")
        
        return training_df


def main():
    """主函数"""
    import argparse
    print("=" * 60)
    print("ChEMBL Web API 数据获取工具")
    print("=" * 60)
    print("\n注意: 使用 Web API 获取数据，无需下载完整数据库")
    print("推荐获取量: 10,000 - 100,000 条记录")

    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50000, help="获取记录数，默认 50000")
    args = parser.parse_args()
    limit = args.limit

    fetcher = ChEMBLAPIFetcher()

    try:
        # 获取数据
        print("\n步骤 1/3: 从 API 获取数据")
        df = fetcher.fetch_activities(limit=limit)

        if len(df) == 0:
            print("未获取到数据")
            return

        # 清洗数据
        print("\n步骤 2/3: 清洗数据")
        clean_df = fetcher.clean_data(df)

        # 保存数据
        print("\n步骤 3/3: 保存训练数据")
        training_df = fetcher.save_training_data(clean_df)

        print("\n" + "=" * 60)
        print("✓ 数据获取完成")
        print(f"✓ 最终数据量: {len(training_df)} 条")
        print("=" * 60)

    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
