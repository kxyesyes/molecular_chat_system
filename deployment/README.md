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

Docker/Compose 基础设施由 operator 管理，并且必须先启动和确认健康；systemd unit
不会启动、重启或修改 Docker/Compose：

```bash
cd /opt/medchat/molecular_chat_system/deployment/temporal
docker compose --env-file /etc/medchat/temporal.env up -d
```

先验证 release commit 和 SHA256，再把相同 release 放入 root-owned、`go-w` 的发布
暂存目录。源码、`/opt/conda/envs/medchat`、安装脚本和已激活 generation 都必须由
`root:root` 持有且不可被 group/other 写入。禁止对 `/opt/medchat` 做递归 `chown`；只有
本节列出的四个运行目录属于 `medchat:medchat 0700`。

安装与激活是两个独立事务。先用非特权 DESTDIR stage 一个不可变、内容寻址的
generation；安装器只写入
`usr/lib/medchat/temporal-worker/releases/<bundle-sha256>` 和安装锁，不创建
`current`、不写生产环境文件，也不 reload、enable、start 或 restart 服务：

```bash
cd /usr/local/src/medchat-release
preview="$PWD/worker-install-preview"
install -d -m 0700 "$preview"
sh deployment/install-temporal-worker.sh --destdir "$preview"
# 记录输出中的 64 位 bundle digest，并检查：
cat "$preview/usr/lib/medchat/temporal-worker/releases/<bundle-sha256>/manifest.sha256"
test ! -e "$preview/usr/lib/medchat/temporal-worker/current"
```

只有 `/usr/local/src/medchat-release` 及其父目录和安装源文件均为 `root:root`、`go-w`
后，才执行 live stage。live stage 仍然不切换当前 generation：

```bash
cd /usr/local/src/medchat-release
sudo deployment/install-temporal-worker.sh --destdir /
```

首次从已知平铺 predecessor 迁移时，必须使用显式 migration。迁移器会先停止并确认
worker/prepare inactive，按内置 hash allowlist 校验旧 unit、helper 和危险旧策略，然后
把危险策略永久移出搜索路径到
`/usr/lib/medchat/temporal-worker/quarantine/legacy-tmpfiles.disabled`。未知 hash、类型、
owner 或 mode 一律 fail closed；失败时绝不恢复危险策略，服务保持 inactive 并要求人工
恢复：

```bash
sudo deployment/activate-temporal-worker-generation.sh \
  --migrate-legacy <bundle-sha256>
```

非 legacy 部署、后续升级和 rollback 都使用同一事务 activation 命令。它验证 generation
目录名、`manifest.sha256` header 和重新计算的 bundle digest 三方一致，停止 worker 后
停止 prepare，原子切换 `current`，执行 daemon-reload，核对 systemd 实际加载内容，再按
prepare、worker 顺序启动。任一步失败都会恢复旧 `current`，并且只恢复原先 active 的
旧服务：

```bash
# 首次激活或升级
sudo deployment/activate-temporal-worker-generation.sh <new-bundle-sha256>

# rollback：目标必须是已存在且完整验证的旧 generation
sudo deployment/activate-temporal-worker-generation.sh <old-bundle-sha256>
```

固定 unit 入口只能是 root-owned symlink，并精确指向
`/usr/lib/medchat/temporal-worker/current/units/`。prepare 的两个 root helper 只能从
`current/libexec/` 执行。不得手工修改 `current`、固定 unit symlink 或 generation 内容。

由 operator 从 `deployment/temporal-worker.env.example` 手工创建专用环境文件：

```bash
sudo install -d -o root -g root -m 0755 /etc/medchat
sudo install -o root -g root -m 0600 deployment/temporal-worker.env.example /etc/medchat/temporal-worker.env
sudoedit /etc/medchat/temporal-worker.env
```

`/etc/medchat/temporal-worker.env` 必须保持 `root:root`、regular、非 symlink、`0600`，
并且 no secrets：不得写入 API key、token、password、loader/runtime 变量或 Web 配置。
首次 activation 已完成 daemon-reload 与启动顺序；如需设置开机启动，只启用 worker：

```bash
sudo systemctl enable medchat-temporal-worker.service
```

每次修改专用环境文件后，必须先通过 prepare helper 重跑 root trust preparation，再重启
非特权 worker：

```bash
sudo systemctl restart medchat-temporal-worker-prepare.service
sudo systemctl restart medchat-temporal-worker.service
```

不要只重启 worker 后假定旧的 prepare 验证仍然覆盖新配置。

#### 当前 release blockers

- isolated live-root runner 已实现 private mount namespace、只读 lower root、tmpfs
  upper/work、overlay merged root 和 chroot 的技术隔离检查，但本轮 Windows 验证严禁执行；
  在 disposable Linux 上以 opt-in 实际运行并通过前仍是 release blocker。
  runner 只能从所有父目录和文件均为 root-owned、`go-w` 的可信 staging 执行；mount
  namespace 不能抵御恶意 root 代码，也不能把不可信发布源码变成可信安装输入。
- staging failure/signal/concurrency 用例在 Windows 上按真实 POSIX 能力 skip；必须在
  native Linux 上直接执行并通过，不能用静态断言替代。
- 真实 systemd activation/migration、`systemd-analyze verify`、mixed lifecycle 和
  runtime-mask + old `cgroup.kill` fd 测试尚未在 disposable production candidate 上执行，
  均为 release blocker。无 cgroup v2 或不可打开 `cgroup.kill` 时没有 PID 或
  `systemctl kill` fallback。

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
