# MedChat 服务器部署环境文档

本文档用于把 MedChat 项目从本地开发环境迁移到服务器并挂网运行。内容覆盖系统环境、Python/conda 环境、药物化学工具链、数据资产、Web 服务、nginx、systemd、权限、健康检查和常见遗漏项。

## 1. 推荐服务器配置

推荐操作系统：

```bash
Ubuntu 22.04 LTS
Ubuntu 24.04 LTS
```

推荐硬件：

```text
CPU: 8 核以上
内存: 32 GB 起步，推荐 64 GB
磁盘: 500 GB 起步，推荐 1 TB SSD
GPU: 非必须；如果后续跑本地大模型或深度学习模型，再配置 NVIDIA GPU
```

推荐部署目录：

```text
/opt/medchat/molecular_chat_system
/opt/medchat/tools
/opt/medchat/data
```

## 2. 系统依赖

服务器先安装基础系统包：

```bash
sudo apt update
sudo apt install -y \
  git curl wget unzip tar ca-certificates \
  build-essential gcc g++ make \
  sqlite3 nginx \
  libglib2.0-0 libxrender1 libxext6 libsm6 libgl1 \
  python3-dev
```

如果后续需要 HTTPS，可再安装：

```bash
sudo apt install -y certbot python3-certbot-nginx
```

## 3. Python / Conda 环境

建议使用 conda 环境，不建议服务器使用 Python 3.13 直接部署。RDKit、FAISS、部分药化依赖在 Python 3.10/3.11 更稳定。

推荐创建环境：

```bash
conda create -n medchat python=3.10 -y
conda activate medchat
```

RTX3090 服务器建议优先安装部署版依赖：

```bash
pip install -r deployment/requirements.txt
```

根目录 `requirements.txt` 更偏通用开发环境；`deployment/requirements.txt` 固定了 CUDA 12.1、PyTorch、PyG、药化和 Web 服务部署所需版本，更适合 RTX3090 服务器。

分子对接还需要安装 AutoDock Vina、ADFRsuite、Meeko/mk_prepare_ligand。Meeko 已包含在 `deployment/requirements.txt` 中，Vina 和 ADFRsuite 需要按服务器系统单独安装，详细步骤见 `deployment/docking_tools.md`。

如果 `rdkit` 或 `faiss-cpu` 通过 pip 安装不稳定，优先使用 conda-forge：

```bash
conda install -c conda-forge rdkit faiss-cpu -y
```

如果后续确实需要 FAISS GPU，可在服务器上改用 conda 安装：

```bash
conda install -c pytorch -c nvidia faiss-gpu -y
```

项目核心 Python 依赖包括：

```text
fastapi
uvicorn[standard]
python-multipart
websockets
aiofiles
pandas
numpy
tqdm
rdkit
faiss-cpu
pydantic
pyyaml
jinja2
requests
httpx
pubchempy
```

## 4. 分子对接工具链

分子对接模块不是纯 Python 功能，需要额外安装外部程序。

必须准备：

```text
AutoDock Vina
ADFRsuite
Meeko / mk_prepare_ligand
```

推荐版本：

```text
AutoDock Vina 1.2.5
ADFRsuite 1.0
Meeko 0.5.0
```

详细安装说明见 `deployment/docking_tools.md`。

示例目录：

```text
/opt/medchat/tools/autodock/vina/vina
/opt/medchat/tools/ADFRsuite/bin/prepare_receptor
/opt/conda/envs/medchat/bin/mk_prepare_ligand.py
```

`.env` 中需要配置：

```env
MOLECULAR_DOCKING_ROOT=/opt/medchat/tools/autodock
MOLECULAR_DOCKING_VINA=/opt/medchat/tools/autodock/vina/vina
MOLECULAR_DOCKING_ADFR_BIN=/opt/medchat/tools/ADFRsuite/bin
MOLECULAR_DOCKING_PREPARE_LIGAND=/opt/conda/envs/medchat/bin/mk_prepare_ligand.py
```

验证命令：

```bash
$MOLECULAR_DOCKING_VINA --help
ls $MOLECULAR_DOCKING_ADFR_BIN/prepare_receptor*
which mk_prepare_ligand.py
```

如果服务器上的 Meeko 命令名是 `mk_prepare_ligand`，不是 `mk_prepare_ligand.py`，就按真实路径填写 `MOLECULAR_DOCKING_PREPARE_LIGAND`。

## 5. 模型服务环境

项目可使用两类模型后端：Ollama 本地模型或 ModelScope API。

### 5.1 Ollama 本地模型

安装并启动：

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama serve
ollama pull qwen2.5:3b
```

检查：

```bash
curl http://127.0.0.1:11434/api/tags
```

默认配置通常使用：

```text
Ollama 地址: http://127.0.0.1:11434
```

### 5.2 ModelScope API

如果使用 ModelScope API，`.env` 中配置：

```env
MODELSCOPE_API_KEY=你的真实key
MODELSCOPE_BASE_URL=https://api-inference.modelscope.cn/v1/chat/completions
```

生产环境不要把 API key 写死在 YAML 文件里，统一放到 `.env`。

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

项目根目录准备 `.env`：

```env
MEDCHAT_ENV_FILE=.env
MEDCHAT_HOST=127.0.0.1
MEDCHAT_PORT=6001
MEDCHAT_DEBUG=false
MEDCHAT_RELOAD=false
MEDCHAT_WORKERS=1
MEDCHAT_LOG_LEVEL=info
MEDCHAT_LOG_DIR=logs
MEDCHAT_TEMP_DOCKING_DIR=temp_docking
MEDCHAT_SCRATCH_DIR=scratch

MODELSCOPE_API_KEY=
MODELSCOPE_BASE_URL=https://api-inference.modelscope.cn/v1/chat/completions

OLLAMA_BASE_URL=http://127.0.0.1:11434

MOLECULAR_DOCKING_ROOT=/opt/medchat/tools/autodock
MOLECULAR_DOCKING_VINA=/opt/medchat/tools/autodock/vina/vina
MOLECULAR_DOCKING_ADFR_BIN=/opt/medchat/tools/ADFRsuite/bin
MOLECULAR_DOCKING_PREPARE_LIGAND=/opt/conda/envs/medchat/bin/mk_prepare_ligand.py

TARGET_DB_PATH=data/target_db/target_database.sqlite
TARGET_CACHE_DIR=data/target_db/cache

CHEMBL_DB_PATH=/opt/medchat/data/chembl/chembl_36.db
REVERSE_TARGET_DATA_DIR=data/reverse_target

ACTIVITY_MODEL_DIR=data/activity/models
RAG_INDEX_PATH=data/molecular_faiss_index.index

MEDCHAT_SYSTEMD_SERVICE=deployment/medchat.service
MEDCHAT_NGINX_CONFIG=deployment/nginx-medchat.conf
```

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

在项目根目录执行：

```bash
conda activate medchat
python scripts/health_check.py --strict
```

理想结果：

```text
RDKit: OK
AutoDock Vina: OK
ADFRsuite: OK
Ligand Preparation: OK
Target DB: OK
Target Cache: OK
Ollama: OK
ModelScope: OK 或 optional
Reverse Target Data: OK
Activity Models: OK
RAG Index: OK
Writable Directories: OK
systemd Service: OK
nginx Config: OK
```

如果只是查看状态，不希望失败时返回非 0 状态码：

```bash
python scripts/health_check.py
```

## 10. 本地启动验证

先不接 nginx，直接启动服务：

```bash
conda activate medchat
python main.py --no-reload
```

检查：

```bash
curl http://127.0.0.1:6001/health
```

浏览器访问：

```text
http://<server-ip>:6001/
http://<server-ip>:6001/target-search
http://<server-ip>:6001/molecular-docking
http://<server-ip>:6001/reverse-target
http://<server-ip>:6001/activity-prediction
http://<server-ip>:6001/molecular-design
```

## 11. systemd 服务

根据服务器真实路径修改 `deployment/medchat.service`：

```text
User
Group
WorkingDirectory
EnvironmentFile
ExecStart
```

安装并启动：

```bash
sudo cp deployment/medchat.service /etc/systemd/system/medchat.service
sudo systemctl daemon-reload
sudo systemctl enable --now medchat
sudo systemctl status medchat
```

常用命令：

```bash
sudo systemctl restart medchat
sudo systemctl stop medchat
sudo journalctl -u medchat -f
```

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

根据服务器域名修改 `deployment/nginx-medchat.conf` 中的：

```text
server_name
client_max_body_size
proxy_pass
```

安装配置：

```bash
sudo cp deployment/nginx-medchat.conf /etc/nginx/conf.d/medchat.conf
sudo nginx -t
sudo systemctl reload nginx
```

注意 `/ws` 必须保留 WebSocket 相关配置：

```text
Upgrade
Connection
```

否则聊天或实时功能可能异常。

## 13. 防火墙和端口

如果使用 UFW：

```bash
sudo ufw allow 22
sudo ufw allow 80
sudo ufw allow 443
sudo ufw enable
```

对公网只暴露 80/443。应用端口建议只监听本机或内网，由 nginx 转发。

## 14. 上线后验证清单

上线后逐项验证：

```text
首页可打开
聊天接口可响应
WebSocket 正常
靶点搜索可查询 WDR5 / EGFR / PDE4D
靶点结构文件可下载
分子对接可上传受体/配体文件
反向寻靶可返回结果
活性预测模型可加载
分子设计页面可打开
日志正常写入
大文件上传不被 nginx 拦截
```

## 15. 常见遗漏项

最容易遗漏：

```text
RDKit 和 FAISS 使用了不兼容的 Python 版本
Vina / ADFRsuite / Meeko 没有安装或路径没填
data/target_db/cache/ 没有迁移
反向寻靶 fingerprint 文件没有迁移
活性预测模型文件没有迁移
RAG index 没有迁移
systemd 的 WorkingDirectory 不对
systemd 的 EnvironmentFile 不对
服务用户没有写 logs/temp_docking/scratch 的权限
nginx 没有配置 /ws WebSocket
nginx client_max_body_size 太小
公网环境没有 HTTPS
API key 写进了 YAML 或代码
```

## 16. 建议上线顺序

推荐顺序：

1. 在服务器创建 conda 环境并安装依赖
2. 迁移代码和数据资产
3. 配置 `.env`
4. 跑 `python scripts/health_check.py --strict`
5. 用 `python main.py --no-reload` 手动启动验证
6. 配置 systemd
7. 配置 nginx
8. 配置 HTTPS
9. 做全模块功能验收
10. 设置日志轮转和数据备份

## 17. 运行时数据库、缓存与日志安全

生产环境建议把靶点数据库和结构缓存放在独立持久化卷，并通过环境变量配置：

```env
TARGET_DB_PATH=/var/lib/medchat/target/target_database.sqlite
TARGET_CACHE_DIR=/var/lib/medchat/target/cache
```

相对路径按项目根目录解析。靶点数据库使用 SQLite WAL、`synchronous=NORMAL`、30 秒 busy timeout 和外键约束；不要在部署脚本中改回 `journal_mode=OFF` 或 `locking_mode=EXCLUSIVE`。

RCSB/AlphaFold 文件会先流式写入同目录临时文件，默认最大 50 MiB。下载内容通过 PDB/mmCIF 基本结构标记校验后才原子发布到正式缓存路径；失败或超限不会更新数据库的 `is_downloaded` 状态。

`main.py` 对 `logs/app.log` 使用追加式轮转日志：单文件 10 MiB，保留 5 个备份。服务用户必须对 `logs/`、`TARGET_DB_PATH` 的父目录及 `TARGET_CACHE_DIR` 具有写权限；这些运行产物不得提交到 Git。
