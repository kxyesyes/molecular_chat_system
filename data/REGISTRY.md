# MedChat Data Asset Registry

本文件记录 MedChat 运行所需的本地数据资产。大型数据库、模型权重、结构缓存和向量索引不应直接提交到 Git；部署时请按本登记表单独放置，并通过 `scripts/health_check.py` 验证。

健康检查标签：`Target DB`、`Target Cache`、`Reverse Target Data`、`Activity Models`、`RAG Index`、`Sample Assets`。

## 轻量样例数据

| 资产 | 路径 | 用途 | 是否进入 Git |
|---|---|---|---|
| 示例小分子 | `data/samples/5.sdf` | 分子处理/上传测试样例 | 是 |
| 示例蛋白结构 | `data/samples/MAGL_5zun.pdb` | 分子对接页面上传测试样例 | 是 |

## 运行期数据资产

| 资产 | 默认路径 | 来源/构建方式 | 验证方式 | Git 策略 |
|---|---|---|---|---|
| 靶点 SQLite 数据库 | `data/target_db/target_database.sqlite` | `python -m src.target_search.seed` 或批量同步脚本 | `python scripts/health_check.py` 的 `Target DB` | 不提交 |
| 靶点结构缓存 | `data/target_db/cache/` | 靶点搜索下载器 / 批量结构同步 | `Target Cache` | 不提交 |
| 反向寻靶 ChEMBL 表 | `data/reverse_target/chembl_data_with_fps.tsv` 或 `chembl_training_data.tsv` | `src/reverse_target/*build*`、`download_chembl.py` 相关脚本 | `Reverse Target Data` | 不提交 |
| 反向寻靶指纹矩阵 | `data/reverse_target/morgan_fingerprints.npy`、`maccs_fingerprints.npy` | `src/reverse_target/generate_fingerprints.py` | `Reverse Target Data` | 不提交 |
| 活性预测模型 | `data/activity/models/` | 训练产物或服务器侧模型目录 | `Activity Models` | 不提交 |
| RAG FAISS 索引 | `data/molecular_faiss_index.index` | RAG 索引构建流程 | `RAG Index` | 不提交 |
| 分子设计导出结果 | `data/design_results/` | 用户运行时保存/导出 | 手动检查 | 不提交 |

## 部署约定

- 生产环境优先通过 `.env` 配置 `TARGET_DB_PATH`、`TARGET_CACHE_DIR`、`REVERSE_TARGET_DATA_DIR`、`ACTIVITY_MODEL_DIR`、`RAG_INDEX_PATH`。
- 数据库、模型、缓存和索引建议放在服务器持久化目录；Git 仓库只保存代码、轻量样例和构建/校验脚本。
- 更新任何数据资产时，应记录来源、版本、生成命令和日期；后续可扩展为带 SHA256 校验的 `scripts/fetch_data.py`。
