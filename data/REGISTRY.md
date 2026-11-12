# MedChat Data Asset Registry

本文件记录 MedChat 运行所需的数据资产。运行数据库、结构缓存和向量索引按登记表部署。模型权重、运行时 registry 和真实训练资产应按明确的来源、许可及提交范围管理。部署文件检查使用 `scripts/health_check.py`。

健康检查标签：`Target DB`、`Target Cache`、`Reverse Target Data`、`Activity Models`、`RAG Index`、`Sample Assets`。

## 轻量样例数据

| 资产 | 路径 | 用途 | 是否进入 Git |
|---|---|---|---|
| 示例小分子 | `data/samples/5.sdf` | 分子处理/上传测试样例 | 是 |
| 示例蛋白结构 | `data/samples/MAGL_5zun.pdb` | 分子对接页面上传测试样例 | 是 |
| PDE4A apo 示例结构（PDB 6FG5） | `data/samples/PDE4A_apo_6FG5.pdb` | 分子对接一键示例；Schistosoma mansoni PDE4A apo | 是 |
| 罗氟司特示例配体 | `data/samples/roflumilast.pdbqt` | 与 6FG5 配套的对接输入构象 | 是 |

罗氟司特沿用对接页原始 SMILES `C1CC1COC2=C(C=CC(=C2)C(=O)NC3=C(C=NC=C3Cl)Cl)OC(F)F`。2026-11-12 使用 RDKit 2025.03.6 读取该中性结构并显式加氢，用 ETKDGv3（`randomSeed=42`）生成单一构象，再用 MMFF94（`maxIters=1000`）优化至返回码 0。用 Meeko 0.6.1 的 `MoleculePreparation().prepare` 默认参数制备配体，用 `PDBQTWriterLegacy.write_string` 导出 ASCII 文件，移除行尾空白并统一为 LF 换行。经 Meeko 回读并移除显式氢后，规范 SMILES 与输入一致。PDBQT 的 SHA-256 为 `c19a0d8de0934fa2a11ddee86a19be4421d2970b793c8e3664992471da398b5c`。

`PDE4A_apo_6FG5.pdb` 来源：RCSB PDB entry 6FG5（下载日期 2026-10-27）；2026-10-28 按 PDB chain ID 提取 A 链，保留原始坐标、ANISOU、A 链 Zn/Mg、水及对应 LINK/CONECT，移除 B 链及原多链汇总记录。原文件 SHA256：`B998745414E398D63F7A7C7036F4C76D4CE3D258272BF50A790B29478A09C50D`；当前单链文件 SHA256：`32AC86832BED79F72418788F5EBD749CDF8EF3BD917557976C53B7FD6EA9FE93`。

使用 AutoDock Vina 1.1.2 验证上述配体可直接参与对接。受体为已制备的 6FG5 A 链，对接盒中心为 (17.32, 15.62, −22.83) Å，边长均为 20 Å，`exhaustiveness=8`、`num_modes=10`、`energy_range=3`、搜索种子为 42，使用 1 个 CPU 线程。此次运行输出 6 个构象，Meeko 可回读全部构象，配体分子图与原始 SMILES 一致。

页面的“载入示例”预设使用 `exhaustiveness=24`、`num_modes=20`，保留 3 kcal/mol 能量窗口。使用与上次相同的受体 PDBQT（SHA-256：`d2b267b8e78c2dba1835d1b3b6c912f62cb124dbfe765628a7872adb64e92988`）、配体、搜索盒、种子和线程数复测，Vina 输出 9 个结合姿势，评分从 −6.9 到 −3.9 kcal/mol；Meeko 回读得到 9 个构象，配体分子图与输入一致。20 是输出上限，页面实际运行的姿势数取决于搜索结果。

## 运行期数据资产

| 资产 | 默认路径 | 来源/构建方式 | 验证方式 | Git 策略 |
|---|---|---|---|---|
| 靶点 SQLite 数据库 | `data/target_db/target_database.sqlite` | `python -m src.target_search.seed` 或批量同步脚本 | `python scripts/health_check.py` 的 `Target DB` | 不提交 |
| 靶点结构缓存 | `data/target_db/cache/` | 靶点搜索下载器 / 批量结构同步 | `Target Cache` | 不提交 |
| 反向寻靶 ChEMBL 表 | `data/reverse_target/chembl_data_with_fps.tsv` 或 `chembl_training_data.tsv` | `src/reverse_target/*build*`、`download_chembl.py` 相关脚本 | `Reverse Target Data` | 不提交 |
| 反向寻靶指纹矩阵 | `data/reverse_target/morgan_fingerprints.npy`、`maccs_fingerprints.npy` | `src/reverse_target/generate_fingerprints.py` | `Reverse Target Data` | 不提交 |
| 活性预测模型（已批准 r2 部署包） | `data/activity/models/pde-buche-20261006-r2/` | 已验收 PDE/BuChE family bundle；模型卡、权重与 v3 registry 一并维护 | `Activity Models` + r2 registry 校验 | 按明确批准范围提交 |
| 其他活性预测模型/训练资产 | `data/activity/models/` 下其他目录 | 本地训练或搜索产物 | `Activity Models` | 确认来源、许可和提交范围后按需登记 |
| RAG FAISS 索引 | `data/molecular_faiss_index.index` | RAG 索引构建流程 | `RAG Index` | 不提交 |
| 分子设计导出结果 | `data/design_results/` | 用户运行时保存/导出 | 手动检查 | 不提交 |
| Agent 历史结果展示明细 | Agent 状态库同级的 `result_details/`（默认 `data/result_details/`） | 仅由已验证结果生成的私有不可变 JSON 快照；不是执行或引用授权 | 所属会话、文件摘要、结构和字节预算读取校验；相关 store/routes 测试 | 运行时数据，不提交 |

## 部署约定

历史反向寻靶 TSV 中，部分非 nM 观测的数值已换算成 nM，单位列仍保留原始单位。仅凭旧 TSV 无法判断每行是否需要换算。对使用旧导出器构建的数据，先保留原版本，核对原始 ChEMBL 观测或质量审计表，再重新导出训练表，并从新表重建指纹和对应 manifest。新版导出器将 `standard_value` 与 `standard_units` 一起规范为 nM，审计表保留来源数值和单位。

读取时遇到具有旧导出特征且数值与单位无法配对的记录，2D 和 3D 结果保留相似度排名，返回 `standard_value: null` 和 `measurement_status: unit_verification_required`。新旧严格回执消费者支持的字段不同，生产端与消费者需配套发布。缺少明确质量资格标记的来源记录返回 `quality_eligible: null`。

- 生产环境优先通过 `.env` 配置 `TARGET_DB_PATH`、`TARGET_CACHE_DIR`、`REVERSE_TARGET_DATA_DIR`、`ACTIVITY_MODEL_DIR`。RAG 使用自定义位置时，同时设置健康检查的 `RAG_INDEX_PATH`（索引文件全名）与运行时 YAML 的 `rag.vector_store_path`（去掉 `.index` 后缀的前缀）。健康检查确认文件存在且非空，加载时再核对索引及 manifest。
- 数据库、未批准模型、缓存和索引建议放在服务器持久化目录；本仓库明确批准的
  `pde-buche-20261006-r2` 部署包例外纳入 Git，并由 registry 摘要校验。
- 更新任何数据资产时，应记录来源、版本、生成命令和日期；后续可扩展为带 SHA256 校验的 `scripts/fetch_data.py`。

## 靶点数据库运行时安全约定

- `TARGET_DB_PATH` 可配置为绝对路径或相对项目根目录的路径；未配置时使用 `data/target_db/target_database.sqlite`。
- `TARGET_CACHE_DIR` 可配置为绝对路径或相对项目根目录的路径；数据库中已有的 `data/target_db/cache/...` 逻辑路径会映射到该目录。
- 靶点 SQLite 连接统一启用 WAL、`synchronous=NORMAL`、30 秒 busy timeout 和外键约束，支持 Web 请求与后台同步并发访问。
- 在线结构下载先流式写入同目录临时文件，限制为 50 MiB，并校验 PDB/mmCIF 标记；只有校验通过后才原子替换正式缓存文件。
- 数据库、WAL 文件、结构缓存与下载临时文件均属于运行时资产，禁止提交到 Git。
