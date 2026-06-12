"""
从本地 ChEMBL 数据库文件构建反向寻靶数据库
支持用户指定文件路径
"""

import sys
import tarfile
from pathlib import Path
import shutil

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from src.reverse_target.extract_clean_data import ChEMBLDataExtractor
from src.reverse_target.generate_fingerprints import FingerprintGenerator


def extract_tar_file(tar_path, output_dir):
    """解压 tar 或 tar.gz 文件"""
    print(f"正在解压: {tar_path}")
    print(f"目标目录: {output_dir}")
    
    try:
        # 自动检测压缩格式
        if str(tar_path).endswith('.tar.gz') or str(tar_path).endswith('.tgz'):
            mode = 'r:gz'
        elif str(tar_path).endswith('.tar'):
            mode = 'r'
        else:
            mode = 'r:*'  # 自动检测
        
        with tarfile.open(tar_path, mode) as tar:
            tar.extractall(path=output_dir)
        print("✓ 解压完成")
        return True
    except Exception as e:
        print(f"✗ 解压失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def find_db_file(search_dir):
    """在目录中查找 .db 文件"""
    db_files = list(Path(search_dir).rglob("*.db"))
    if db_files:
        return db_files[0]
    return None


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='从本地文件构建反向寻靶数据库')
    parser.add_argument('--tar-file', type=str, help='ChEMBL tar 文件路径')
    parser.add_argument('--db-file', type=str, help='ChEMBL .db 文件路径（如果已解压）')
    parser.add_argument('--skip-extract', action='store_true', help='跳过数据提取（使用已有的 training 文件）')
    parser.add_argument('--skip-fingerprint', action='store_true', help='跳过指纹生成')
    
    args = parser.parse_args()
    
    output_dir = Path("data/reverse_target")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    print("=" * 80)
    print("反向寻靶数据库构建工具（本地文件版）")
    print("=" * 80)
    
    db_path = None
    
    # 步骤 1: 处理文件
    if args.db_file:
        # 直接使用提供的 .db 文件
        db_path = Path(args.db_file)
        if not db_path.exists():
            print(f"✗ 数据库文件不存在: {db_path}")
            return
        print(f"\n使用数据库文件: {db_path}")
    
    elif args.tar_file:
        # 解压 tar 文件
        tar_path = Path(args.tar_file)
        if not tar_path.exists():
            print(f"✗ tar 文件不存在: {tar_path}")
            return
        
        print("\n步骤 1: 解压 tar 文件")
        if extract_tar_file(tar_path, output_dir):
            # 查找解压出的 .db 文件
            db_path = find_db_file(output_dir)
            if db_path:
                print(f"✓ 找到数据库文件: {db_path}")
            else:
                print("✗ 未找到 .db 文件")
                return
    else:
        # 尝试自动查找
        print("\n正在搜索现有的数据库文件...")
        db_path = find_db_file(output_dir)
        if db_path:
            print(f"✓ 找到数据库文件: {db_path}")
        else:
            print("✗ 未找到数据库文件")
            print("\n请使用以下选项之一：")
            print("  --tar-file <路径>  指定 tar 文件路径")
            print("  --db-file <路径>   指定 .db 文件路径")
            print("\n示例：")
            print("  python src/reverse_target/build_from_file.py --tar-file data/reverse_target/chembl_33_sqlite.tar")
            print("  python src/reverse_target/build_from_file.py --db-file data/reverse_target/chembl_33/chembl_33.db")
            return
    
    # 步骤 2: 提取和清洗数据
    if not args.skip_extract:
        print("\n步骤 2: 提取和清洗数据")
        print("-" * 80)
        
        try:
            extractor = ChEMBLDataExtractor(db_path=str(db_path))
            
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
            
        except Exception as e:
            print(f"\n✗ 数据提取失败: {e}")
            import traceback
            traceback.print_exc()
            return
    else:
        print("\n⊙ 跳过数据提取步骤")
    
    # 步骤 3: 生成分子指纹
    if not args.skip_fingerprint:
        print("\n步骤 3: 生成分子指纹")
        print("-" * 80)
        
        try:
            generator = FingerprintGenerator()
            
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
            
        except Exception as e:
            print(f"\n✗ 指纹生成失败: {e}")
            import traceback
            traceback.print_exc()
            return
    else:
        print("\n⊙ 跳过指纹生成步骤")
    
    # 完成
    print("\n" + "=" * 80)
    print("✓ 反向寻靶数据库构建完成！")
    print("=" * 80)
    print(f"\n输出目录: {output_dir.absolute()}")
    print("\n核心文件:")
    print("  - chembl_training_data.tsv       训练数据表")
    print("  - morgan_fingerprints.npy        Morgan 指纹")
    print("  - maccs_fingerprints.npy         MACCS 指纹")
    print("  - chembl_data_with_fps.tsv       完整数据表")
    print("=" * 80)


if __name__ == "__main__":
    main()
