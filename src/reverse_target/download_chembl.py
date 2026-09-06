"""
ChEMBL v36 数据库下载脚本
从 ChEMBL FTP 服务器下载数据库文件
"""

import os
import requests
from pathlib import Path
from tqdm import tqdm

if __package__ in {None, ""}:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.reverse_target.config import configure_console_output, get_reverse_target_data_dir


class ChEMBLDownloader:
    def __init__(self, output_dir=None):
        self.output_dir = Path(output_dir) if output_dir else get_reverse_target_data_dir()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # ChEMBL v33 下载链接
        self.base_url = "https://ftp.ebi.ac.uk/pub/databases/chembl/ChEMBLdb/releases/chembl_36/"
        
        # 需要下载的核心文件（SQLite 数据库）
        self.files = {
            "chembl_36_sqlite.tar.gz": "chembl_36_sqlite.tar.gz"
        }
    
    def download_file(self, url, output_path):
        """下载单个文件，带进度条"""
        print(f"正在下载: {url}")
        
        response = requests.get(url, stream=True)
        response.raise_for_status()
        
        total_size = int(response.headers.get('content-length', 0))
        block_size = 8192
        
        with open(output_path, 'wb') as f:
            with tqdm(total=total_size, unit='B', unit_scale=True) as pbar:
                for chunk in response.iter_content(chunk_size=block_size):
                    if chunk:
                        f.write(chunk)
                        pbar.update(len(chunk))
        
        print(f"下载完成: {output_path}")
    
    def download_chembl(self):
        """下载 ChEMBL 数据库"""
        for filename, save_name in self.files.items():
            url = self.base_url + filename
            output_path = self.output_dir / save_name
            
            if output_path.exists():
                print(f"文件已存在: {output_path}")
                continue
            
            try:
                self.download_file(url, output_path)
            except Exception as e:
                print(f"下载失败: {filename}")
                print(f"错误: {e}")
                return False
        
        return True
    
    def extract_database(self):
        """解压数据库文件"""
        import tarfile
        
        tar_path = self.output_dir / "chembl_36_sqlite.tar.gz"
        
        if not tar_path.exists():
            print(f"未找到压缩文件: {tar_path}")
            return False
        
        print(f"正在解压: {tar_path}")
        
        try:
            with tarfile.open(tar_path, 'r:gz') as tar:
                tar.extractall(path=self.output_dir)
            print(f"解压完成")
            return True
        except Exception as e:
            print(f"解压失败: {e}")
            return False


def main():
    """主函数"""
    configure_console_output()
    print("=" * 60)
    print("ChEMBL v33 数据库下载工具")
    print("=" * 60)
    
    downloader = ChEMBLDownloader()
    
    # 下载数据库
    print("\n步骤 1/2: 下载数据库文件")
    if not downloader.download_chembl():
        print("下载失败，请检查网络连接")
        return
    
    # 解压数据库
    print("\n步骤 2/2: 解压数据库文件")
    if not downloader.extract_database():
        print("解压失败")
        return
    
    print("\n" + "=" * 60)
    print("✓ ChEMBL v36 数据库下载完成")
    print(f"✓ 数据存储位置: {downloader.output_dir}")
    print("=" * 60)


if __name__ == "__main__":
    main()
