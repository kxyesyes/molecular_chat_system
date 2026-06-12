# 反向寻靶数据库构建工具

> Active implementation note: the runtime reverse-target module is
> `src/reverse_target`. The older `src/target_reverse` prototype has been
> archived under `archive/legacy_target_reverse` and must not be imported by
> web routes, API handlers, or Agent tools.

本工具用于构建基于 ChEMBL v36 数据库的反向寻靶系统，包括数据下载、提取清洗和分子指纹生成。

## 功能特性

- ✅ 自动下载 ChEMBL v36 数据库（~3 GB）
- ✅ 提取化合物-靶点-活性核心数据
- ✅ 数据清洗和标准化（单位转换、生物体名称标准化）
- ✅ 生成 Morgan (ECFP4) 和 MACCS 分子指纹
- ✅ 支持分步执行和完整流程

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

确保安装了以下关键依赖：
- rdkit
- pandas
- numpy
- tqdm
- requests

### 2. 运行完整构建流程

```bash
python src/reverse_target/build_database.py
```

**预计耗时**: 30-60 分钟（取决于网速和电脑性能）

### 3. 如果已有数据库文件，跳过下载步骤

```bash
python src/reverse_target/build_database.py --skip-download
```

### 4. 只运行特定步骤

```bash
# 只下载数据库
python src/reverse_target/build_database.py --step 1

# 只提取和清洗数据
python src/reverse_target/build_database.py --step 2

# 只生成指纹
python src/reverse_target/build_database.py --step 3

# 只显示摘要
python src/reverse_target/build_database.py --step 4
```

## 输出文件

构建完成后，所有文件将保存在 `data/reverse_target/` 目录下：

### 核心数据文件

| 文件名 | 描述 | 格式 |
|--------|------|------|
| `chembl_training_data.tsv` | 清洗后的训练数据 | TSV |
| `chembl_data_with_fps.tsv` | 带指纹的完整数据 | TSV |
| `morgan_fingerprints.npy` | Morgan 指纹矩阵 | NumPy |
| `maccs_fingerprints.npy` | MACCS 指纹矩阵 | NumPy |
| `fingerprint_metadata.pkl` | 元数据信息 | Pickle |

### 数据格式示例

**chembl_training_data.tsv**:
```
molecule_chembl_id	canonical_smiles	target_name	standard_type	standard_value	organism
CHEMBL25	CC(=O)OC1=CC=CC=C1C(=O)O	COX-1	IC50	5600.0	Human
```

### 指纹参数

- **Morgan 指纹 (ECFP4)**:
  - Radius: 2
  - Bits: 2048
  - 类型: Binary fingerprint

- **MACCS 指纹**:
  - Bits: 166
  - 类型: Structural keys

## 模块说明

### 1. download_chembl.py

下载 ChEMBL v36 数据库：
- 从 EBI FTP 服务器下载 SQLite 数据库
- 自动解压缩
- 断点续传支持

```python
from src.reverse_target import ChEMBLDownloader

downloader = ChEMBLDownloader()
downloader.download_chembl()
downloader.extract_database()
```

### 2. extract_clean_data.py

提取和清洗数据：
- 查询化合物-靶点-活性关系
- 过滤无效数据
- 标准化单位（转换为 nM）
- 标准化生物体名称

```python
from src.reverse_target import ChEMBLDataExtractor

extractor = ChEMBLDataExtractor()
raw_df = extractor.extract_raw_data()
clean_df = extractor.clean_data(raw_df)
training_df = extractor.format_training_data(clean_df)
```

### 3. generate_fingerprints.py

生成分子指纹：
- Morgan (ECFP4) 指纹
- MACCS 指纹
- 批量处理和进度显示

```python
from src.reverse_target import FingerprintGenerator

generator = FingerprintGenerator()
df = generator.load_data()
valid_df, morgan_fps, maccs_fps = generator.process_molecules(df)
generator.save_fingerprints(valid_df, morgan_fps, maccs_fps)
```

### 4. build_database.py

主流程整合脚本：
- 完整流程自动化
- 支持分步执行
- 错误处理和进度反馈

```python
from src.reverse_target import DatabaseBuilder

builder = DatabaseBuilder()
builder.build_full_pipeline()
```

## 数据统计

典型的 ChEMBL v36 数据集包含：
- **化合物数量**: ~200,000+
- **唯一靶点**: ~2,000+
- **活性记录**: ~1,000,000+

活性类型分布：
- IC50: 抑制浓度
- EC50: 有效浓度
- Ki: 抑制常数
- Kd: 解离常数

## 常见问题

### Q1: 下载速度慢怎么办？

A: ChEMBL 数据库约 3 GB，建议：
1. 使用稳定的网络连接
2. 或手动下载后放到 `data/reverse_target/` 目录
3. 使用 `--skip-download` 参数跳过下载

### Q2: 内存不足怎么办？

A: 如果处理大量数据时内存不足：
1. 修改 SQL 查询中的 `LIMIT` 参数减少数据量
2. 分批处理数据

### Q3: RDKit 安装失败？

A: RDKit 依赖 conda，建议：
```bash
conda install -c conda-forge rdkit
```

### Q4: 如何只处理人类靶点数据？

A: 在 `extract_clean_data.py` 中取消注释：
```python
df = df[df['organism'] == 'Human']
```

## 进阶使用

### 自定义指纹参数

编辑 `generate_fingerprints.py`：
```python
self.morgan_radius = 3  # 改为 ECFP6
self.morgan_bits = 4096  # 增加位数
```

### 添加更多活性类型

编辑 `extract_clean_data.py` 的 SQL 查询：
```sql
AND act.standard_type IN ('IC50', 'EC50', 'Ki', 'Kd', 'AC50')
```

### 导出为其他格式

```python
import pandas as pd
df = pd.read_csv('data/reverse_target/chembl_training_data.tsv', sep='\t')
df.to_csv('output.csv', index=False)  # CSV
df.to_parquet('output.parquet')  # Parquet
```

## 许可证

本工具使用 ChEMBL 数据库，遵循 ChEMBL 的使用条款。

## 参考

- ChEMBL 数据库: https://www.ebi.ac.uk/chembl/
- RDKit 文档: https://www.rdkit.org/docs/
