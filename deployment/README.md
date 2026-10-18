# MedChat 服务器部署环境文档

本文是部署模板与生产运行约束的入口。首次部署按 [Ubuntu 单机部署教程](../docs/DEPLOYMENT_TUTORIAL.md) 执行；本页保留资产要求、权限、Temporal worker 和上线门禁，不重复整套安装步骤。

两种部署布局不可混用：教程以 home 目录中的 `user` 账号为例；本页 Temporal worker 使用 root-owned 的 `/opt/medchat/molecular_chat_system` 和 `/opt/conda/envs/medchat`。先确定实际服务账号和路径，再配置 unit。

## 1. 推荐服务器配置

参考配置：Ubuntu 22.04/24.04 LTS、8 核 CPU、32 GB 内存（推荐 64 GB）、500 GB 磁盘（推荐 1 TB SSD）。GPU 按本地模型需求配置；这些是容量起点，不是性能保证。

通用模板使用 `/opt/medchat/molecular_chat_system`（代码）、`/opt/medchat/tools`（工具）、`/opt/medchat/data`（数据）。Temporal 的目录权限另见第 8、11.1 节。

## 2. 系统依赖

安装命令集中在 [教程第 1 节](../docs/DEPLOYMENT_TUTORIAL.md#1-登录服务器并准备基础软件)，包括系统库、nginx 和证书工具。公网部署使用 HTTPS/WSS。

## 3. Python / Conda 环境

按 [教程第 3 节](../docs/DEPLOYMENT_TUTORIAL.md#3-创建-python-环境并安装依赖) 创建 Python 3.10 环境。依赖只选择适用的一套：

- [根 requirements.txt](../requirements.txt)：CPU/CI 开发环境。
- [deployment/requirements.txt](requirements.txt)：Linux/CUDA 12.1/RTX3090 部署环境。

不要机械混装两套 profile。RDKit/FAISS 安装兼容性和基础导入检查见教程；确需 FAISS GPU 时按目标环境单独验证。

## 4. 分子对接工具链

Vina、ADFRsuite 是外部程序，Meeko 是 Python 配体准备工具。版本、安装命令、路径环境变量和验证方法统一见 [docking_tools.md](docking_tools.md)，不要只安装 Python 依赖就认为对接已就绪。

## 5. 模型服务环境

主聊天模型的用户级配置、服务账号、Key 保留/清除和升级行为统一见 [README](../README.md#主模型接入与一次保存)。不能用旧 `MODELSCOPE_*` 或 `OPENAI_COMPATIBLE_*` 环境变量替代首页配置；独立验收 CLI 的显式环境接口不受影响。

分子生成独立使用 Ollama `gmm-llama:latest`；安装与检查见 [教程第 4.1 节](../docs/DEPLOYMENT_TUTORIAL.md#41-ollama-本地模型)。其他通用聊天模型不能替代它。

## 6. 必须迁移的数据资产

挂网部署时不能只迁移代码，必须迁移数据文件、模型文件和结构缓存。

### 6.1 靶点搜索模块

必须包含：

```text
data/target_db/target_database.sqlite
data/target_db/cache/
data/target_db/seed_targets.csv
data/target_db/seed_structures.csv
```

`.env` 配置：

```env
TARGET_DB_PATH=data/target_db/target_database.sqlite
TARGET_CACHE_DIR=data/target_db/cache
```

如果结构缓存没有迁移，靶点搜索页面可以查到靶点，但下载 PDB/mmCIF 时可能失败。

### 6.2 反向寻靶模块

通常需要迁移：

```text
data/reverse_target/
```

重点检查是否存在：

```text
chembl_training_data.tsv
chembl_data_with_fps.tsv
morgan_fingerprints.npy
maccs_fingerprints.npy
metadata / summary 文件
```

如果使用完整 ChEMBL SQLite 数据库，建议配置：

```env
CHEMBL_DB_PATH=/opt/medchat/data/chembl/chembl_36.db
```

聊天反向寻靶在应用启动时对完整来源逐行重算 Morgan/MACCS 指纹并核对原始
popcount，完成后才发布私有紧凑来源。文件存在、metadata 声称已对齐或健康检查
通过，不等于聊天来源已经 READY。校验期间聊天立即返回“来源正在进行全量科学
校验”，不会等待预热或在完成后自动重放请求；完成后需重新发起查询。

服务端可配置独立预热期限（默认 1800 秒，仅接受 120–3600 的十进制整数）：

```env
REVERSE_TARGET_PREWARM_TIMEOUT_SECONDS=1800
```

该参数不延长单次聊天查询期限，也不放宽旧 strict 接口限制。紧凑来源逻辑预算
为快照 768 MiB、获取峰值 1 GiB、单次查询 128 MiB，最多两个并发查询；这些是
受控分配限额，不是进程 RSS 上限，仍需为原生库、映射页及其他服务留出内存。
任何坏行、缓存不一致、预算耗尽或预热超时均不发布部分来源，也不自动重试。
修复或更换资产后由管理员显式重启应用；禁止抽样、裁剪数据或关闭科学校验。
应用关闭会撤销发布资格并等待实际预热线程退出；原生调用尚未返回时可能需要
等待，不应把清理中状态视为已释放。独立反向寻靶页面的 legacy 按需加载不变。

聊天对接必须使用单 worker：仅当 `MEDCHAT_CHAT_DOCKING_ENABLED=1`（严格等于
`1`，其他值仍按原逻辑禁用）时启用聊天对接。其 WebSocket 任务归属及 HTTP
controls 的任务集合是 process-local，多进程分流会使控制请求落到无该任务的进程，
产生随机 404。`main.py` 在加载 env-file 并解析 CLI `--workers` / 环境变量
`MEDCHAT_WORKERS` 后，对聊天对接加多 worker（N > 1）执行 fail-fast，非零退出；
该检查在 reload 的 worker 调整及环境探测、应用装配、服务器启动之前执行，
不会自动降为单 worker 或关闭聊天对接。请显式使用 `--workers 1` 或
`MEDCHAT_WORKERS=1`（CLI 优先）。聊天对接未启用时，其他功能保留以下原有
`main.py` 多 worker 支持边界。

上述自动拒绝保证属于 `main.py` 启动入口。应用无法可靠获知外部 Uvicorn
CLI 或其他进程管理器配置的 worker 数量；`MEDCHAT_WORKERS` 不能作为其实际
进程数的证明。直接 ASGI 启用聊天对接也必须由部署方保证单 worker，不能将
`main.py` 的 guard 当作外部入口的多 worker 检测。

标准 `python main.py` 单 worker 和直接
`python -m uvicorn src.web.app:app` 单 worker 在应用导入时初始化 RDKit
诊断路由。`python main.py --workers N`（或 `MEDCHAT_WORKERS=N`）的每个
spawn 子进程在 `main.py` 以 `__mp_main__` 重导入时先初始化，再进入 Uvicorn
的进程目标函数及 heartbeat 线程；不依赖父进程已初始化，也不豁免任何线程。
直接 `python -m uvicorn src.web.app:app --workers N`（N > 1）没有该引导钩子，
不属于支持的多 worker 启动方式：首次应用导入时已有 heartbeat 线程会拒绝初始化；
请改用上述 `main.py` 启动方式。其他进程管理器须提供同等的每子进程早期引导。

嵌入式使用紧凑来源时，必须在主线程、所有 Python **及原生**工作线程
启动前调用 `src.system.rdkit_logging.initialize_rdkit_logging()`，并且之后不得
切换 RDKit 日志接收器。已知存在 Python 工作线程时的首次初始化会拒绝执行，
不会在并发请求中重置全局日志开关；原生线程是否空闲无法通过 Python 枚举验证，
因此仍由嵌入方保证启动顺序。初始化保留首次 RDKit 导入前已有的日志配置；
未配置命名 logger 时保留 RDKit 默认设置。私有诊断过滤仅作用于调用线程。

### 6.3 活性预测模块

通常需要迁移：

```text
data/activity/
data/activity/models/
```

重点检查：

```text
*.pt
*.pkl
*.joblib
*.json
```

### 6.4 RAG / 分子数据库

通常需要迁移：

```text
data/canonical_moses_5w.csv
data/molecular_faiss_index.index
```

如果 FAISS index 没有迁移，服务可以启动，但 RAG 检索可能不可用或需要重建。

### 6.5 临时目录和日志目录

需要存在并可写：

```text
logs/
temp_docking/
scratch/
```

## 7. `.env` 完整示例

环境变量名称以 [.env.example](../.env.example) 为模板；服务器环境文件的创建、权限和路径示例统一见 [教程第 6 节](../docs/DEPLOYMENT_TUTORIAL.md#6-创建生产环境文件)。复制时使用实际服务账号与路径，不能将教程的 home 目录套到 Temporal worker。

首页主模型保存在服务账号的仓库外私有配置目录，不写进本节的 Web 运行参数模板。Key 不得进入 Git、日志或报告。

### 7.1 当前 HTTP 映射（无 HTTPS 终止）

如果公网映射 `http://medchat.tdd-lab.com` 直接转发到服务器的
`127.0.0.1:6000`，而映射平台没有提供 HTTPS 终止，请在项目根目录的 `.env`
中加入下面这一行：

```env
MEDCHAT_INSECURE_HTTP_HOSTS=medchat.tdd-lab.com
```

仓库提供了不含密钥的示例文件
[`deployment/medchat-http.env.example`](medchat-http.env.example)。它不会被
`main.py --env-file .env` 自动加载，也不会覆盖现有 `.env`；请将该行合并到
`/home/user/XIAOKX/molecular_chat_system/.env`，保留 `.env` 中已有的模型密钥和
其他本机配置。该设置只允许精确的 Host，其他域名仍会返回
`{"detail":"HTTPS required"}`。

应用配置更新后，用现有的手动启动方式重启：

```bash
cd /home/user/XIAOKX/molecular_chat_system
python main.py --env-file .env --host 127.0.0.1 --port 6000 --no-reload
```

该设置是明文 HTTP 的显式例外，仅适合已接受传输风险的临时或受控网络；长期
公网部署仍应配置 HTTPS/WSS。

## 8. 权限配置

建议创建独立用户运行服务，但不要把整个 `/opt/medchat` 交给服务用户：

```bash
sudo useradd -r -m -d /opt/medchat medchat
```

已验证的发布源码 `/opt/medchat/molecular_chat_system`、Conda 环境
`/opt/conda/envs/medchat` 以及安装后的 systemd 和 helper 资产必须由
`root:root` 持有并保持 `go-w`。源码和 Conda 不得由 `medchat` 用户、group 或
other 写入。

Temporal docking worker 只允许 `scratch`、`scratch/task_inputs`、
`scratch/temporal_backups` 和 `temp_docking` 四个运行目录由 `medchat` 持有，且权限固定
为 `0700`。operator 不得手工创建或修复这些目录；只能通过已激活 generation 中的
`prepare-temporal-worker-directories`，由 prepare/activation helper 以 descriptor-relative、
no-follow 方式创建或验证。

不要递归放宽源码树权限。Web 服务需要的其他可写目录应按其独立运行契约逐项配置，
不能扩大 Temporal worker 的写边界。

## 9. 启动前健康检查

在已激活的部署 Python 环境、仓库根目录执行：

```bash
python scripts/health_check.py --env-file /etc/medchat/medchat.env --strict
```

环境文件路径须与实际部署一致。去掉 `--strict` 可仅查看状态。各失败项的处理见 [教程第 7 节](../docs/DEPLOYMENT_TUTORIAL.md#7-运行健康检查)；检查通过不替代真实科学验收或生产恢复验证。

## 10. 本地启动验证

按 [教程第 8 节](../docs/DEPLOYMENT_TUTORIAL.md#8-先绕过-nginx-做本机验证) 先验证应用，再接 nginx。后端为 `127.0.0.1:6000`，浏览器通过本地 6001 SSH 隧道或 HTTPS 域名访问，不关闭受限端口保护。

日常启动优先使用 `main.py`，由它加载环境、检查目录并选择配置；可用参数见 `python main.py --help`。

## 11. systemd 服务

Web unit 的字段、安装与运维命令见 [教程第 9 节](../docs/DEPLOYMENT_TUTORIAL.md#9-配置-systemd)，通用模板为 [medchat.service](medchat.service)。核对 `User`、`Group`、`WorkingDirectory`、`EnvironmentFile` 和 `ExecStart`。

教程的 home 目录单机 Web 配置不能替代下面的 Temporal worker 可信安装流程。

### 11.1 Temporal docking worker 的可信安装

完整命令、逐步验证和回滚以 [Temporal canary 运行手册](../docs/runbooks/temporal_docking_canary.md) 为唯一操作来源。本节只保留生产边界、入口和 release blocker，避免与运行手册维护两份流程。

Docker/Compose 由 operator 管理并先确认健康；systemd unit 不启动、重启或修改 Docker/Compose。发布前必须核验 release commit/SHA256，并确保源码、`/opt/conda/envs/medchat`、安装脚本和已激活 generation 均为 `root:root`、`go-w`；禁止递归 `chown`，只有受控运行目录属于 `medchat:medchat 0700`。

可信安装入口是 `deployment/install-temporal-worker.sh --destdir`，会生成
`run/medchat-temporal-worker/install.lock` 和内容寻址的 `releases/<bundle-sha256>`，并核对
`manifest.sha256`；激活、rollback 和 legacy 迁移统一使用
`activate-temporal-worker-generation.sh`，`--migrate-legacy` 失败必须 fail closed，危险策略移出搜索路径到
`legacy-tmpfiles.disabled`。固定 unit 只能是 root-owned symlink，不能手工修改 `current` 或 generation。

由 operator 从 `deployment/temporal-worker.env.example` 手工创建专用环境文件：

```bash
sudo install -d -o root -g root -m 0755 /etc/medchat
sudo install -o root -g root -m 0600 deployment/temporal-worker.env.example /etc/medchat/temporal-worker.env
sudoedit /etc/medchat/temporal-worker.env
```

`/etc/medchat/temporal-worker.env` 必须保持 `root:root`、regular、非 symlink、`0600`，并且 no secrets：不得写入 API key、token、password、loader/runtime 变量或 Web 配置。首次 activation 已完成 daemon-reload 与启动顺序；如需设置开机启动，只启用 worker：

```bash
sudo systemctl enable medchat-temporal-worker.service
```

每次修改专用环境文件后，必须先通过 prepare helper 重跑 root trust preparation，再重启非特权 worker：

```bash
sudo systemctl restart medchat-temporal-worker-prepare.service
sudo systemctl restart medchat-temporal-worker.service
```

不要只重启 worker 后假定旧的 prepare 验证仍然覆盖新配置。

#### 当前 release blockers

- isolated live-root runner 的 private mount namespace、只读 lower root、tmpfs upper/work、overlay merged root 和 chroot 检查，必须在 disposable Linux opt-in 实跑；Windows 禁止冒充通过，mount namespace 不能抵御恶意 root 代码，也不能把不可信源码变成可信输入。
- staging failure/signal/concurrency 用例若在 Windows 因 POSIX 能力 skip，必须在 native Linux 直接通过，不能用静态断言替代。
- disposable production candidate 尚未完成真实 systemd activation/migration、`systemd-analyze verify`、mixed lifecycle 和 runtime-mask + old `cgroup.kill` fd 测试前，均为 release blocker；无 cgroup v2 或不可打开 `cgroup.kill` 时不得使用 PID 或 `systemctl kill` fallback。

## 12. nginx 反向代理

配置、证书和验证步骤见 [教程第 10 节](../docs/DEPLOYMENT_TUTORIAL.md#10-配置-nginx-和-httpswss)，模板为 [nginx-medchat.conf](nginx-medchat.conf)。修改真实域名、证书、上游和上传限制后，先 `nginx -t` 再 reload；`/ws` 必须保留 WebSocket 升级头。

## 13. 防火墙和端口

公网业务使用 nginx 80/443；SSH 使用实际配置端口，并按运维来源限制访问。不要暴露应用 6000、Ollama 11434 和内部数据库；防火墙步骤见 [教程第 10.3 节](../docs/DEPLOYMENT_TUTORIAL.md#103-防火墙)。

## 14. 上线后验证清单

基础 Web 与科学功能的逐项检查见 [教程第 11 节](../docs/DEPLOYMENT_TUTORIAL.md#11-上线验收)，完成标准见 [第 14 节](../docs/DEPLOYMENT_TUTORIAL.md#14-部署完成标准)。首页可打开不等于科学资产、真实工具或后台任务恢复已通过验收。

## 15. 常见遗漏项

先查 [教程故障排查](../docs/DEPLOYMENT_TUTORIAL.md#13-常见故障排查)：依赖 profile、工具路径、数据/缓存、服务账号权限、WebSocket 和上传限制。Temporal worker 不得通过递归放宽权限绕过失败，须遵守第 8、11.1 节。

## 16. 建议上线顺序

基础单机步骤以部署教程为准；更新、备份和回滚见 [部署教程](../docs/DEPLOYMENT_TUTORIAL.md)第 12 节。按实际启用能力再检查以下门禁。

如果部署要求进程重启后仍能恢复后台任务，应在生产环境设置：

```env
MEDCHAT_REQUIRE_DURABLE_TASKS=1
```

该开关会让 `python scripts/health_check.py --strict` 在任务后端仍为
`local` 时失败；生产环境应改用已通过健康检查的 Temporal 后端。开发环境
不设置该变量时，local 后端仍可用于单进程调试，但其结果不代表具备重启恢复能力。

如果生产环境要提供真实的 PDE/BuChE 活性预测，还应设置：

```env
MEDCHAT_REQUIRE_ACTIVE_ACTIVITY_MODELS=1
```

该开关会要求根活动模型 registry 已经原子选择并验证 PDE、BuChE 两个
family bundle。只有子目录中存在训练注册记录、但根 registry 尚未选择
active bundle 时，严格健康检查也会失败；不会自动替换或激活模型。

## 17. 运行时数据库、缓存与日志安全

生产环境建议把靶点数据库和结构缓存放在独立持久化卷，并通过环境变量配置：

```env
TARGET_DB_PATH=/var/lib/medchat/target/target_database.sqlite
TARGET_CACHE_DIR=/var/lib/medchat/target/cache
```

相对路径按项目根目录解析。靶点数据库使用 SQLite WAL、`synchronous=NORMAL`、30 秒 busy timeout 和外键约束；不要在部署脚本中改回 `journal_mode=OFF` 或 `locking_mode=EXCLUSIVE`。

RCSB/AlphaFold 文件会先流式写入同目录临时文件，默认最大 50 MiB。下载内容通过 PDB/mmCIF 基本结构标记校验后才原子发布到正式缓存路径；失败或超限不会更新数据库的 `is_downloaded` 状态。

`main.py` 对 `logs/app.log` 使用追加式轮转日志：单文件 10 MiB，保留 5 个备份。服务用户必须对 `logs/`、`TARGET_DB_PATH` 的父目录及 `TARGET_CACHE_DIR` 具有写权限；这些运行产物不得提交到 Git。
