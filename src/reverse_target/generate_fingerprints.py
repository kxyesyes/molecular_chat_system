"""
分子指纹生成脚本
使用 RDKit 为每个分子生成 Morgan 和 MACCS 指纹
"""

import pandas as pd
import numpy as np
from pathlib import Path
from rdkit import Chem
from rdkit import DataStructs
from rdkit.Chem import AllChem, MACCSkeys
from tqdm import tqdm
import pickle

from src.reverse_target.config import get_reverse_target_data_dir


def _bitvect_to_numpy_array(bitvect):
    num_bits = bitvect.GetNumBits()
    arr = np.zeros((num_bits,), dtype=np.uint8)
    try:
        DataStructs.ConvertToNumpyArray(bitvect, arr)
        return arr
    except (TypeError, ValueError):
        return np.fromiter(
            (1 if bitvect.GetBit(index) else 0 for index in range(num_bits)),
            dtype=np.uint8,
            count=num_bits,
        )


def _process_single_fp(smiles):
    """用于多进程提取指纹的包装函数 (Top-level)"""
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem, MACCSkeys
        from rdkit import DataStructs
        import numpy as np
        
        mol = Chem.MolFromSmiles(smiles)
        if mol is None: return None, None
        
        # Morgan (Radius 2, 2048 bits)
        fp_morgan = AllChem.GetMorganFingerprintAsBitVect(mol, radius=2, nBits=2048)
        arr_morgan = np.zeros((fp_morgan.GetNumBits(),), dtype=np.uint8)
        try:
            DataStructs.ConvertToNumpyArray(fp_morgan, arr_morgan)
        except (TypeError, ValueError):
            arr_morgan = np.fromiter(
                (1 if fp_morgan.GetBit(index) else 0 for index in range(fp_morgan.GetNumBits())),
                dtype=np.uint8,
                count=fp_morgan.GetNumBits(),
            )
        
        # MACCS (166 bits)
        fp_maccs = MACCSkeys.GenMACCSKeys(mol)
        arr_maccs_full = np.zeros((fp_maccs.GetNumBits(),), dtype=np.uint8)
        try:
            DataStructs.ConvertToNumpyArray(fp_maccs, arr_maccs_full)
        except (TypeError, ValueError):
            arr_maccs_full = np.fromiter(
                (1 if fp_maccs.GetBit(index) else 0 for index in range(fp_maccs.GetNumBits())),
                dtype=np.uint8,
                count=fp_maccs.GetNumBits(),
            )
        arr_maccs = arr_maccs_full[1:]
        
        return arr_morgan, arr_maccs
    except:
        return None, None


class FingerprintGenerator:
    def __init__(self, input_file=None):
        self.input_file = Path(input_file) if input_file else (
            get_reverse_target_data_dir() / "chembl_training_data.tsv"
        )
        self.output_dir = self.input_file.parent
        
        if not self.input_file.exists():
            raise FileNotFoundError(f"输入文件不存在: {self.input_file}")
        
        # 指纹参数
        self.morgan_radius = 2  # ECFP4
        self.morgan_bits = 2048
        self.maccs_bits = 166
    
    def load_data(self):
        """加载训练数据"""
        print(f"加载数据: {self.input_file}")
        df = pd.read_csv(self.input_file, sep='\t')
        print(f"数据量: {len(df)} 条")
        return df
    
    def smiles_to_mol(self, smiles):
        """SMILES 转换为分子对象"""
        try:
            mol = Chem.MolFromSmiles(smiles)
            return mol
        except:
            return None
    
    def generate_morgan_fingerprint(self, mol):
        """生成 Morgan 指纹 (ECFP4)"""
        if mol is None:
            return None
        
        try:
            fp = AllChem.GetMorganFingerprintAsBitVect(
                mol, 
                radius=self.morgan_radius, 
                nBits=self.morgan_bits
            )
            # 转换为 numpy 数组
            return _bitvect_to_numpy_array(fp)
        except:
            return None
    
    def generate_maccs_fingerprint(self, mol):
        """生成 MACCS 指纹"""
        if mol is None:
            return None
        
        try:
            fp = MACCSkeys.GenMACCSKeys(mol)
            # 转换为 numpy 数组 (RDKit MACCS 为 167 bits，丢弃第0位得到 166 bits)
            arr_full = _bitvect_to_numpy_array(fp)
            arr = arr_full[1:]
            return arr
        except:
            return None
    
    def process_molecules(self, df):
        """处理所有分子，生成指纹 (多进程优化版)"""
        print("\n生成分子指纹 (多进程模式)...")
        
        from concurrent.futures import ProcessPoolExecutor
        import os
        
        # 准备数据
        smiles_list = df['canonical_smiles'].tolist()
        indices = df.index.tolist()
        
        # 使用 cpu_count - 1 个进程
        num_workers = max(1, os.cpu_count() - 1)
        print(f"使用 {num_workers} 个进程进行并行计算...")
        
        morgan_fps_list = []
        maccs_fps_list = []
        valid_indices = []
        
        with ProcessPoolExecutor(max_workers=num_workers) as executor:
            # 提交任务
            results = list(tqdm(executor.map(_process_single_fp, smiles_list), total=len(smiles_list), desc="并行处理分子"))
            
            # 收集结果
            for idx, (m_fp, ma_fp) in enumerate(results):
                if m_fp is not None:
                    morgan_fps_list.append(m_fp)
                    maccs_fps_list.append(ma_fp)
                    valid_indices.append(indices[idx])
        
        print(f"\n成功生成 {len(valid_indices)} 个分子的指纹")
        
        # 转换为 numpy 数组
        morgan_fps = np.array(morgan_fps_list)
        maccs_fps = np.array(maccs_fps_list)
        
        # 过滤有效数据
        valid_df = df.iloc[valid_indices].reset_index(drop=True)
        
        return valid_df, morgan_fps, maccs_fps
    
    def save_fingerprints(self, df, morgan_fps, maccs_fps):
        """保存指纹数据"""
        print("\n保存指纹数据...")
        
        # 1. 保存为 numpy 格式
        morgan_npy = self.output_dir / "morgan_fingerprints.npy"
        maccs_npy = self.output_dir / "maccs_fingerprints.npy"
        
        np.save(morgan_npy, morgan_fps)
        np.save(maccs_npy, maccs_fps)
        
        print(f"✓ Morgan 指纹已保存: {morgan_npy}")
        print(f"✓ MACCS 指纹已保存: {maccs_npy}")
        
        # 2. 保存处理后的数据表
        df_output = self.output_dir / "chembl_data_with_fps.tsv"
        df.to_csv(df_output, sep='\t', index=False)
        print(f"✓ 数据表已保存: {df_output}")
        
        # 3. 保存元数据
        metadata = {
            'morgan_radius': self.morgan_radius,
            'morgan_bits': self.morgan_bits,
            'maccs_bits': self.maccs_bits,
            'num_molecules': len(df),
            'num_unique_molecules': df['molecule_chembl_id'].nunique(),
            'num_targets': df['target_name'].nunique()
        }
        
        metadata_file = self.output_dir / "fingerprint_metadata.pkl"
        with open(metadata_file, 'wb') as f:
            pickle.dump(metadata, f)
        
        print(f"✓ 元数据已保存: {metadata_file}")
        
        # 4. 保存指纹示例（前 100 个）
        sample_data = {
            'molecule_chembl_id': df['molecule_chembl_id'].head(100).tolist(),
            'canonical_smiles': df['canonical_smiles'].head(100).tolist(),
            'morgan_fps': morgan_fps[:100].tolist(),
            'maccs_fps': maccs_fps[:100].tolist()
        }
        
        sample_file = self.output_dir / "fingerprint_samples.pkl"
        with open(sample_file, 'wb') as f:
            pickle.dump(sample_data, f)
        
        print(f"✓ 示例数据已保存: {sample_file}")
        
        return metadata
    
    def generate_summary(self, metadata):
        """生成摘要报告"""
        print("\n" + "=" * 60)
        print("指纹生成摘要")
        print("=" * 60)
        print(f"Morgan 指纹参数: radius={metadata['morgan_radius']}, bits={metadata['morgan_bits']}")
        print(f"MACCS 指纹参数: bits={metadata['maccs_bits']}")
        print(f"处理分子数: {metadata['num_molecules']}")
        print(f"唯一分子数: {metadata['num_unique_molecules']}")
        print(f"唯一靶点数: {metadata['num_targets']}")
        print("=" * 60)


def main():
    """主函数"""
    print("=" * 60)
    print("分子指纹生成工具")
    print("=" * 60)
    
    try:
        generator = FingerprintGenerator()
        
        # 加载数据
        print("\n步骤 1/3: 加载数据")
        df = generator.load_data()
        
        # 生成指纹
        print("\n步骤 2/3: 生成指纹")
        valid_df, morgan_fps, maccs_fps = generator.process_molecules(df)
        
        # 保存结果
        print("\n步骤 3/3: 保存结果")
        metadata = generator.save_fingerprints(valid_df, morgan_fps, maccs_fps)
        
        # 生成摘要
        generator.generate_summary(metadata)
        
        print("\n✓ 指纹生成完成")
        
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
