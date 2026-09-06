"""
反向寻靶数据库构建主流程脚本
整合下载、提取清洗、指纹生成的完整流程
"""

import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from src.reverse_target.download_chembl import ChEMBLDownloader
from src.reverse_target.extract_clean_data import ChEMBLDataExtractor
from src.reverse_target.generate_fingerprints import FingerprintGenerator
from src.reverse_target.config import get_chembl_db_path, get_reverse_target_data_dir


class DatabaseBuilder:
    """反向寻靶数据库构建器"""
    
    def __init__(self):
        self.output_dir = get_reverse_target_data_dir()
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    def step1_download(self):
        """步骤 1: 下载 ChEMBL 数据库"""
        print("\n" + "=" * 80)
        print("步骤 1/4: 下载 ChEMBL v36 数据库")
        print("=" * 80)
        
        downloader = ChEMBLDownloader(output_dir=self.output_dir)
        
        # 下载数据库
        print("\n> 下载数据库文件...")
        if not downloader.download_chembl():
            print("✗ 下载失败")
            return False
        
        # 解压数据库
        print("\n> 解压数据库文件...")
        if not downloader.extract_database():
            print("✗ 解压失败")
            return False
        
        print("\n✓ ChEMBL 数据库下载完成")
        return True
    
    def step2_extract_clean(self):
        """步骤 2: 提取和清洗数据"""
        print("\n" + "=" * 80)
        print("步骤 2/4: 提取和清洗数据")
        print("=" * 80)
        
        try:
            extractor = ChEMBLDataExtractor(db_path=get_chembl_db_path())
            
            # 提取原始数据
            print("\n> 提取原始数据...")
            raw_df = extractor.extract_raw_data()
            
            # 清洗数据
            print("\n> 清洗数据...")
            clean_df = extractor.clean_data(raw_df)
            
            # 格式化训练数据
            print("\n> 格式化训练数据...")
            training_df = extractor.format_training_data(clean_df)
            
            print(f"\n✓ 数据提取和清洗完成 (共 {len(training_df)} 条)")
            return True
            
        except Exception as e:
            print(f"\n✗ 数据提取失败: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def step3_generate_fingerprints(self):
        """步骤 3: 生成分子指纹"""
        print("\n" + "=" * 80)
        print("步骤 3/4: 生成分子指纹")
        print("=" * 80)
        
        try:
            generator = FingerprintGenerator(
                input_file=self.output_dir / "chembl_training_data.tsv"
            )
            
            # 加载数据
            print("\n> 加载训练数据...")
            df = generator.load_data()
            
            # 生成指纹
            print("\n> 生成 Morgan 和 MACCS 指纹...")
            valid_df, morgan_fps, maccs_fps = generator.process_molecules(df)
            
            # 保存结果
            print("\n> 保存指纹数据...")
            metadata = generator.save_fingerprints(valid_df, morgan_fps, maccs_fps)
            
            generator.generate_summary(metadata)
            
            print("\n✓ 指纹生成完成")
            return True
            
        except Exception as e:
            print(f"\n✗ 指纹生成失败: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def step4_summary(self):
        """步骤 4: 生成最终摘要"""
        print("\n" + "=" * 80)
        print("步骤 4/4: 生成最终摘要")
        print("=" * 80)
        
        import pickle
        
        # 读取元数据
        metadata_file = self.output_dir / "fingerprint_metadata.pkl"
        if metadata_file.exists():
            with open(metadata_file, 'rb') as f:
                metadata = pickle.load(f)
            
            print("\n数据库构建完成！")
            print("-" * 80)
            print(f"输出目录: {self.output_dir.absolute()}")
            print(f"\n核心文件:")
            print(f"  1. chembl_training_data.tsv       - 训练数据表")
            print(f"  2. chembl_data_with_fps.tsv       - 带指纹的数据表")
            print(f"  3. morgan_fingerprints.npy        - Morgan 指纹 ({metadata['morgan_bits']} bits)")
            print(f"  4. maccs_fingerprints.npy         - MACCS 指纹 ({metadata['maccs_bits']} bits)")
            print(f"  5. fingerprint_metadata.pkl       - 元数据")
            
            print(f"\n数据统计:")
            print(f"  - 分子数量: {metadata['num_molecules']:,}")
            print(f"  - 唯一分子: {metadata['num_unique_molecules']:,}")
            print(f"  - 唯一靶点: {metadata['num_targets']:,}")
            
            print(f"\n指纹参数:")
            print(f"  - Morgan: ECFP4 (radius={metadata['morgan_radius']}, {metadata['morgan_bits']} bits)")
            print(f"  - MACCS: {metadata['maccs_bits']} bits")
            print("-" * 80)
        else:
            print("✗ 未找到元数据文件")
    
    def build_full_pipeline(self, skip_download=False):
        """运行完整的构建流程"""
        print("=" * 80)
        print("反向寻靶数据库构建工具")
        print("=" * 80)
        print("\n此工具将执行以下步骤:")
        print("  1. 下载 ChEMBL v36 数据库 (~3 GB)")
        print("  2. 提取化合物-靶点-活性数据")
        print("  3. 清洗和格式化数据")
        print("  4. 生成分子指纹 (Morgan + MACCS)")
        print("\n预计耗时: 30-60 分钟（取决于网速和电脑性能）")
        
        input("\n按 Enter 键开始构建，或按 Ctrl+C 取消...")
        
        # 步骤 1: 下载（可选跳过）
        if not skip_download:
            if not self.step1_download():
                print("\n✗ 构建失败：下载步骤出错")
                return False
        else:
            print("\n⊙ 跳过下载步骤（使用现有数据库）")
        
        # 步骤 2: 提取和清洗
        if not self.step2_extract_clean():
            print("\n✗ 构建失败：数据提取步骤出错")
            return False
        
        # 步骤 3: 生成指纹
        if not self.step3_generate_fingerprints():
            print("\n✗ 构建失败：指纹生成步骤出错")
            return False
        
        # 步骤 4: 摘要
        self.step4_summary()
        
        print("\n" + "=" * 80)
        print("✓ 反向寻靶数据库构建完成！")
        print("=" * 80)
        
        return True


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='反向寻靶数据库构建工具')
    parser.add_argument('--skip-download', action='store_true', 
                        help='跳过下载步骤（使用现有数据库）')
    parser.add_argument('--step', type=int, choices=[1, 2, 3, 4],
                        help='只运行指定步骤 (1=下载, 2=提取, 3=指纹, 4=摘要)')
    
    args = parser.parse_args()
    
    builder = DatabaseBuilder()
    
    try:
        if args.step:
            # 运行单个步骤
            if args.step == 1:
                builder.step1_download()
            elif args.step == 2:
                builder.step2_extract_clean()
            elif args.step == 3:
                builder.step3_generate_fingerprints()
            elif args.step == 4:
                builder.step4_summary()
        else:
            # 运行完整流程
            builder.build_full_pipeline(skip_download=args.skip_download)
    
    except KeyboardInterrupt:
        print("\n\n用户取消操作")
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ 发生错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
