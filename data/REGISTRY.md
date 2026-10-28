# MedChat Data Asset Registry

本文件记录 MedChat 运行所需的本地数据资产。大型数据库、模型权重、结构缓存和向量索引不应直接提交到 Git；部署时请按本登记表单独放置，并通过 `scripts/health_check.py` 验证。

健康检查标签：`Target DB`、`Target Cache`、`Reverse Target Data`、`Activity Models`、`RAG Index`、`Sample Assets`。

## 轻量样例数据

| 资产 | 路径 | 用途 | 是否进入 Git |
|---|---|---|---|
| 示例小分子 | `data/samples/5.sdf` | 分子处理/上传测试样例 | 是 |
| 示例蛋白结构 | `data/samples/MAGL_5zun.pdb` | 分子对接页面上传测试样例 | 是 |
| PDE4A apo 示例结构（PDB 6FG5） | `data/samples/PDE4A_apo_6FG5.pdb` | 分子对接一键示例；Schistosoma mansoni PDE4A apo | 是 |

`PDE4A_apo_6FG5.pdb` 来源：RCSB PDB entry 6FG5（下载日期 2026-10-27）；SHA256：`B998745414E398D63F7A7C7036F4C76D4CE3D258272BF50A790B29478A09C50D`。

## 运行期数据资产

| 资产 | 默认路径 | 来源/构建方式 | 验证方式 | Git 策略 |
|---|---|---|---|---|
| 靶点 SQLite 数据库 | `data/target_db/target_database.sqlite` | `python -m src.target_search.seed` 或批量同步脚本 | `python scripts/health_check.py` 的 `Target DB` | 不提交 |
| 靶点结构缓存 | `data/target_db/cache/` | 靶点搜索下载器 / 批量结构同步 | `Target Cache` | 不提交 |
| 反向寻靶 ChEMBL 表 | `data/reverse_target/chembl_data_with_fps.tsv` 或 `chembl_training_data.tsv` | `src/reverse_target/*build*`、`download_chembl.py` 相关脚本 | `Reverse Target Data` | 不提交 |
| 反向寻靶指纹矩阵 | `data/reverse_target/morgan_fingerprints.npy`、`maccs_fingerprints.npy` | `src/reverse_target/generate_fingerprints.py` | `Reverse Target Data` | 不提交 |
| 活性预测模型（已批准 r2 部署包） | `data/activity/models/pde-buche-20261006-r2/` | 已验收 PDE/BuChE family bundle；模型卡、权重与 v3 registry 一并维护 | `Activity Models` + r2 registry 校验 | 按明确批准范围提交；其他训练产物不提交 |
| 其他活性预测模型/训练产物 | `data/activity/models/` 下其他目录 | 本地训练或搜索输出 | `Activity Models` | 不提交 |
| RAG FAISS 索引 | `data/molecular_faiss_index.index` | RAG 索引构建流程 | `RAG Index` | 不提交 |
| 分子设计导出结果 | `data/design_results/` | 用户运行时保存/导出 | 手动检查 | 不提交 |
| Agent 历史结果展示明细 | Agent 状态库同级的 `result_details/`（默认 `data/result_details/`） | 仅由已验证结果生成的私有不可变 JSON 快照；不是执行或引用授权 | 所属会话、文件摘要、结构和字节预算读取校验；相关 store/routes 测试 | 运行时数据，不提交 |

## 部署约定

- 生产环境优先通过 `.env` 配置 `TARGET_DB_PATH`、`TARGET_CACHE_DIR`、`REVERSE_TARGET_DATA_DIR`、`ACTIVITY_MODEL_DIR`、`RAG_INDEX_PATH`。
- 数据库、未批准模型、缓存和索引建议放在服务器持久化目录；本仓库明确批准的
  `pde-buche-20261006-r2` 部署包例外纳入 Git，并由 registry 摘要校验。
- 更新任何数据资产时，应记录来源、版本、生成命令和日期；后续可扩展为带 SHA256 校验的 `scripts/fetch_data.py`。

## 靶点数据库运行时安全约定

- `TARGET_DB_PATH` 可配置为绝对路径或相对项目根目录的路径；未配置时使用 `data/target_db/target_database.sqlite`。
- `TARGET_CACHE_DIR` 可配置为绝对路径或相对项目根目录的路径；数据库中已有的 `data/target_db/cache/...` 逻辑路径会映射到该目录。
- 靶点 SQLite 连接统一启用 WAL、`synchronous=NORMAL`、30 秒 busy timeout 和外键约束，支持 Web 请求与后台同步并发访问。
- 在线结构下载先流式写入同目录临时文件，限制为 50 MiB，并校验 PDB/mmCIF 标记；只有校验通过后才原子替换正式缓存文件。
- 数据库、WAL 文件、结构缓存与下载临时文件均属于运行时资产，禁止提交到 Git。
