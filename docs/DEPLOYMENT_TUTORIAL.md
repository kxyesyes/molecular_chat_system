# MedChat 部署教程

conda run -n medchat-admet-ai python -m pip install \
  -r /home/user/XIAOKX/molecular_chat_system/requirements-admet-ai-140.txt

conda run -n medchat-admet-ai python -c \
  "import sys; print(sys.executable); print(sys.version)"

本文档面向一台 Ubuntu 22.04/24.04 服务器，目标是完成一套可维护的单机部署：

~~~text
浏览器 --HTTPS/WSS--> nginx --HTTP/WS--> MedChat (127.0.0.1:6001)
                                      |
                                      +--> Ollama（可选，本机 127.0.0.1:11434）
                                      +--> Vina / ADFRsuite / Meeko（按需）
                                      +--> SQLite、模型、FAISS 和结构缓存
~~~

本文档不把“代码能启动”当作科学功能已经可用。没有真实模型、权重、数据库或外部科学工具时，服务应明确报告 unavailable/failed，不能用示例数据冒充真实预测。

## 0. 部署边界和约定

- 本服务器项目目录固定为：/home/user/XIAOKX/molecular_chat_system。
- 本教程按登录用户 user 运行 systemd；项目位于该用户的 home 目录时，不要直接套用 medchat 用户模板，否则可能没有权限穿过 /home/user。
- 应用只监听 127.0.0.1:6001，公网只暴露 nginx 的 80/443。
- 生产环境关闭自动重载，使用 python main.py --no-reload。
- deployment/requirements.txt 是 Linux/CUDA 12.1/RTX3090 服务器 profile；根目录 requirements.txt 是 CPU/CI profile。两者不要混装。
- 默认 MEDCHAT_TASK_BACKEND=local 适合单机开发和基础验证，不代表进程重启后后台任务一定能恢复。需要持久化任务时，必须另外部署并验收 Temporal；见 deployment/temporal/ 和 deployment/README.md。

文档中的以下值都要替换成自己的真实值：

~~~text
<server-user>       user
<project-dir>       /home/user/XIAOKX/molecular_chat_system
<domain>            解析到服务器的域名，例如 medchat.example.com
<github-repo>       GitHub 仓库地址
<python-bin>        conda 环境中 python -c "import sys; print(sys.executable)" 的输出
<tools-dir>         Vina/ADFRsuite 等外部工具的实际安装目录
<vina-path>         which vina 的输出
<meeko-path>        which mk_prepare_ligand.py 或 which mk_prepare_ligand 的输出
~~~

不要把 API key、密码、证书私钥或用户聊天内容写进 Git、报告和日志。

## 1. 登录服务器并准备基础软件

使用普通部署账号登录，不建议直接用 root 做日常开发：

~~~bash
ssh <server-user>@<server-ip>
~~~

安装系统依赖：

~~~bash
sudo apt update
sudo apt install -y \
  git curl wget ca-certificates unzip tar \
  build-essential gcc g++ make \
  sqlite3 nginx \
  libglib2.0-0 libxrender1 libxext6 libsm6 libgl1 \
  python3-dev \
  certbot python3-certbot-nginx
~~~

如果使用 GPU，先确认驱动可见：

~~~bash
nvidia-smi
~~~

如果 nvidia-smi 不存在或失败，不要把 CUDA profile 当作已验证；可以先使用 CPU profile，或先按服务器发行版安装并验证 NVIDIA 驱动。

## 2. 配置 GitHub SSH 并拉取代码

### 2.1 首次配置 SSH

在服务器上生成密钥：

~~~bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh
ssh-keygen -t ed25519 -C "medchat-deploy@<server-host>"
~~~

默认路径直接按回车即可：

~~~text
~/.ssh/id_ed25519       私钥，只能留在服务器上
~/.ssh/id_ed25519.pub   公钥，可添加到 GitHub
~~~

加载私钥并测试：

~~~bash
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519
ssh -T git@github.com
~~~

首次连接会询问 GitHub 主机指纹。确认指纹来自可信渠道后再输入 yes。成功时应看到类似：

~~~text
Hi <github-user>! You've successfully authenticated, but GitHub does not provide shell access.
~~~

如果仍是 Permission denied (publickey)，检查：

~~~bash
ssh-add -l
ssh-keygen -lf ~/.ssh/id_ed25519.pub -E sha256
ssh -T -i ~/.ssh/id_ed25519 -o IdentitiesOnly=yes git@github.com
~~~

把下面命令的完整输出复制到 GitHub 账号的 Settings → SSH and GPG keys，不要复制私钥：

~~~bash
cat ~/.ssh/id_ed25519.pub
~~~

公钥必须添加到拥有目标仓库权限的账号；如果仓库属于 Organization，还要按该 Organization 的要求授权 SSO。

### 2.2 克隆仓库

项目目录已经确定为 /home/user/XIAOKX/molecular_chat_system。首次部署时执行：

~~~bash
mkdir -p /home/user/XIAOKX
cd /home/user/XIAOKX
test -d molecular_chat_system/.git || git clone <github-repo> molecular_chat_system
cd /home/user/XIAOKX/molecular_chat_system
~~~

例如 SSH 仓库地址通常是：

~~~text
git@github.com:<github-user>/<repository>.git
~~~

确认当前版本：

~~~bash
git status --short --branch
git log -1 --oneline
~~~

部署时应记录 commit SHA，后续升级和回滚都使用明确的 commit，而不是未记录的工作树。

## 3. 创建 Python 环境并安装依赖

### 3.1 推荐的 Conda 方式

服务器已有 Conda 时：

~~~bash
conda create -n medchat python=3.10 -y
conda activate medchat
~~~

GPU/CUDA 12.1 服务器安装部署 profile：

~~~bash
cd /home/user/XIAOKX/molecular_chat_system
python -m pip install --upgrade pip
python -m pip install -r deployment/requirements.txt
~~~

如果 rdkit 或 faiss-cpu 的 pip wheel 与当前环境不兼容，可改用：

~~~bash
conda install -c conda-forge rdkit faiss-cpu -y
~~~

CPU/开发服务器使用根目录 profile：

~~~bash
python -m pip install -r requirements.txt
~~~

不要在同一个环境中先安装两套 profile 再用“最后安装的版本”碰运气解决冲突。

### 3.2 验证 Python 和基础导入

~~~bash
python --version
python -c "import fastapi, uvicorn, httpx, yaml, jinja2, websockets; print('python imports: OK')"
python -m compileall -q src scripts
~~~

## 4. 安装可选的科学工具链

基础聊天页面不一定需要全部工具；需要对应功能时再安装并验证。

### 4.1 Ollama 本地模型

按 Ollama 官方安装方式安装后，启动服务：

~~~bash
ollama serve
~~~

另开一个终端检查：

~~~bash
curl -fsS http://127.0.0.1:11434/api/tags
ollama list
~~~

项目默认分子生成模型名为 gmm-llama:latest。只有在模型真实存在并经实际调用验证后，才在环境文件中启用它：

~~~env
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=gmm-llama:latest
~~~

不要让主聊天模型配置覆盖分子生成工具的模型配置。

### 4.2 AutoDock Vina、ADFRsuite 和 Meeko

Meeko 是 Python 依赖；Vina 和 ADFRsuite 是外部程序。安装细节见 deployment/docking_tools.md。至少要分别验证：

~~~bash
conda activate medchat
conda install -c conda-forge -c bioconda vina=1.2.5 -y
pip install meeko==0.5.0

which vina
vina --help
which mk_prepare_ligand.py || which mk_prepare_ligand
ls <tools-dir>/ADFRsuite/bin/prepare_receptor*
~~~

把服务器上实际存在的路径写入环境文件；不要照抄不存在的路径：

~~~env
MOLECULAR_DOCKING_ROOT=<tools-dir>/autodock
MOLECULAR_DOCKING_VINA=<vina-path>
MOLECULAR_DOCKING_ADFR_BIN=<tools-dir>/ADFRsuite/bin
MOLECULAR_DOCKING_PREPARE_RECEPTOR=<tools-dir>/ADFRsuite/bin/prepare_receptor
MOLECULAR_DOCKING_PREPARE_LIGAND=<meeko-path>
~~~

## 5. 准备持久化目录和数据资产

本机项目位于 user 的 home 目录，本教程让 systemd 也使用 user 运行。先创建项目运行目录：

~~~bash
PROJECT_DIR=/home/user/XIAOKX/molecular_chat_system
mkdir -p "$PROJECT_DIR"/logs \
  "$PROJECT_DIR"/scratch \
  "$PROJECT_DIR"/temp_docking \
  "$PROJECT_DIR"/data/target_db/cache \
  "$PROJECT_DIR"/data/reverse_target \
  "$PROJECT_DIR"/data/activity/models
chmod 750 "$PROJECT_DIR"/logs "$PROJECT_DIR"/scratch "$PROJECT_DIR"/temp_docking
~~~

把代码、模型和数据分开管理。运行所需的大型资产不应提交 Git：

| 功能 | 需要准备的资产 | 推荐环境变量 |
|---|---|---|
| 靶点搜索 | target_database.sqlite、结构缓存 | TARGET_DB_PATH、TARGET_CACHE_DIR |
| 反向寻靶 | ChEMBL TSV、Morgan/MACCS 指纹 | REVERSE_TARGET_DATA_DIR |
| 活性预测 | 已验证模型和 registry | ACTIVITY_MODEL_DIR |
| RAG | FAISS index | RAG_INDEX_PATH |
| 后台任务 | SQLite 任务库 | MEDCHAT_TASK_DB_PATH |

这些资产的来源、版本、生成命令和 SHA256 应单独记录；清单见 data/REGISTRY.md。例如从受控备份复制到服务器：

~~~bash
PROJECT_DIR=/home/user/XIAOKX/molecular_chat_system
rsync -a --info=progress2 <backup>/target_database.sqlite "$PROJECT_DIR/data/target_db/"
rsync -a --info=progress2 <backup>/target-cache/ "$PROJECT_DIR/data/target_db/cache/"
rsync -a --info=progress2 <backup>/reverse_target/ "$PROJECT_DIR/data/reverse_target/"
rsync -a --info=progress2 <backup>/activity-models/ "$PROJECT_DIR/data/activity/models/"
rsync -a --info=progress2 <backup>/molecular_faiss_index.index "$PROJECT_DIR/data/"
~~~

如果没有靶点数据库，可在确认数据来源和许可后构建轻量种子库：

~~~bash
conda activate medchat
python -m src.target_search.seed
~~~

这条命令不能替代完整生产数据资产。

## 6. 创建生产环境文件

创建由当前部署用户 user 读取、同时可被 systemd 加载的环境文件：

~~~bash
sudo install -d -o root -g root -m 0755 /etc/medchat
sudo touch /etc/medchat/medchat.env
sudo chown user:user /etc/medchat/medchat.env
sudo chmod 600 /etc/medchat/medchat.env
sudoedit /etc/medchat/medchat.env
~~~

本教程的 systemd unit 使用 User=user，因此环境文件必须让 user 可读。若以后把服务迁移到独立的 medchat 用户，应改为 root:medchat、权限 640，并同步调整项目目录和运行目录权限。

基础模板如下。空值保持为空，不要填入示例 key：

~~~env
# Web
MEDCHAT_ENV_FILE=/etc/medchat/medchat.env
MEDCHAT_HOST=127.0.0.1
MEDCHAT_PORT=6001
MEDCHAT_DEBUG=false
MEDCHAT_RELOAD=false
MEDCHAT_WORKERS=1
MEDCHAT_LOG_LEVEL=info
MEDCHAT_FORWARDED_ALLOW_IPS=127.0.0.1
MEDCHAT_LOG_DIR=/home/user/XIAOKX/molecular_chat_system/logs
MEDCHAT_TEMP_DOCKING_DIR=/home/user/XIAOKX/molecular_chat_system/temp_docking
MEDCHAT_SCRATCH_DIR=/home/user/XIAOKX/molecular_chat_system/scratch

# Main LLM: choose ollama, modelscope, or openai_compatible
MEDCHAT_LLM_PROVIDER=ollama
MEDCHAT_LLM_STREAM=true
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=gmm-llama:latest

# ModelScope, only when MEDCHAT_LLM_PROVIDER=modelscope
MODELSCOPE_BASE_URL=https://api-inference.modelscope.cn/v1/chat/completions
MODELSCOPE_MODEL=
MODELSCOPE_API_KEY=

# OpenAI-compatible provider, only when selected
OPENAI_COMPATIBLE_BASE_URL=
OPENAI_COMPATIBLE_MODEL=
OPENAI_COMPATIBLE_API_KEY=

# Persistent data assets; use real paths only when the assets exist
TARGET_DB_PATH=/home/user/XIAOKX/molecular_chat_system/data/target_db/target_database.sqlite
TARGET_CACHE_DIR=/home/user/XIAOKX/molecular_chat_system/data/target_db/cache
REVERSE_TARGET_DATA_DIR=/home/user/XIAOKX/molecular_chat_system/data/reverse_target
ACTIVITY_MODEL_DIR=/home/user/XIAOKX/molecular_chat_system/data/activity/models
RAG_INDEX_PATH=/home/user/XIAOKX/molecular_chat_system/data/molecular_faiss_index.index
MEDCHAT_TASK_DB_PATH=/home/user/XIAOKX/molecular_chat_system/scratch/tasks.sqlite

# Docking tools; replace each path with the verified path on this server
MOLECULAR_DOCKING_ROOT=<tools-dir>/autodock
MOLECULAR_DOCKING_VINA=<vina-path>
MOLECULAR_DOCKING_ADFR_BIN=<tools-dir>/ADFRsuite/bin
MOLECULAR_DOCKING_PREPARE_RECEPTOR=<tools-dir>/ADFRsuite/bin/prepare_receptor
MOLECULAR_DOCKING_PREPARE_LIGAND=<meeko-path>

# Development/local task backend. Do not call this restart-durable production.
MEDCHAT_TASK_BACKEND=local
~~~

环境文件写完后检查权限，但不要把内容打印到终端：

~~~bash
sudo stat -c '%U:%G %a %n' /etc/medchat/medchat.env
~~~

## 7. 运行健康检查

先在仓库根目录运行非严格检查，查看缺失项：

~~~bash
cd /home/user/XIAOKX/molecular_chat_system
conda activate medchat
python scripts/health_check.py
~~~

准备好全部要求的真实资产后再运行严格检查：

~~~bash
python scripts/health_check.py --env-file /etc/medchat/medchat.env --strict
~~~

严格检查失败时，不要把失败项改成“可选”来获得绿色结果。按输出逐项处理：

- Ollama：检查 Ollama 服务、URL 和模型名；
- Target DB/Target Cache：检查绝对路径、文件权限和结构缓存；
- Reverse Target Data：检查 TSV 和指纹矩阵是否完整；
- Activity Models：检查模型 registry、权重和 readiness；
- RAG Index：检查 FAISS index 是否存在并可读；
- AutoDock Vina/ADFRsuite/Ligand Preparation：检查外部程序和环境文件路径；
- Writable Directories：只给 medchat 必需的运行目录写权限；
- systemd Service/nginx Config：检查仓库内配置文件是否完整。

## 8. 先绕过 nginx 做本机验证

直接启动一次，确认应用本身正常：

~~~bash
cd /home/user/XIAOKX/molecular_chat_system
conda activate medchat
python main.py --env-file /etc/medchat/medchat.env --no-reload
~~~

另开终端执行：

~~~bash
curl -fsS http://127.0.0.1:6001/health
~~~

浏览器暂时只从服务器本机或 SSH 隧道访问：

~~~bash
ssh -L 6001:127.0.0.1:6001 <server-user>@<server-ip>
~~~

然后打开 http://127.0.0.1:6001/。验证完成后回到运行服务的终端按 Ctrl+C 停止。

## 9. 配置 systemd

仓库中的 deployment/medchat.service 是通用模板，默认仍是 /opt 路径，不能在本服务器上原样复制。先找出 Conda 环境的真实 Python：

~~~bash
conda activate medchat
python -c "import sys; print(sys.executable)"
~~~

例如输出可能是 /home/user/miniconda3/envs/medchat/bin/python。使用该真实路径创建本服务器的 unit：

~~~bash
sudoedit /etc/systemd/system/medchat.service
~~~

写入以下内容，并把 <python-bin> 替换为上一条命令的实际输出：

~~~ini
[Unit]
Description=MedChat Web Service
After=network.target

[Service]
Type=simple
User=user
Group=user
WorkingDirectory=/home/user/XIAOKX/molecular_chat_system
EnvironmentFile=/etc/medchat/medchat.env
ExecStart=<python-bin> main.py --no-reload
Restart=always
RestartSec=3
TimeoutStartSec=120
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
~~~

保存后执行：

~~~bash
sudo systemctl daemon-reload
sudo systemctl enable --now medchat
~~~

查看状态和日志：

~~~bash
sudo systemctl status medchat --no-pager
sudo journalctl -u medchat -n 100 --no-pager
sudo journalctl -u medchat -f
~~~

服务启动后再次验证：

~~~bash
curl -fsS http://127.0.0.1:6001/health
~~~

常用运维命令：

~~~bash
sudo systemctl restart medchat
sudo systemctl stop medchat
sudo systemctl disable medchat
~~~

## 10. 配置 nginx 和 HTTPS/WSS

### 10.1 DNS 和证书

先把 <domain> 的 DNS A/AAAA 记录指向服务器，并确认服务器安全组允许 80/443。新服务器尚未运行站点时，可以用 standalone 方式申请证书：

~~~bash
sudo systemctl stop nginx
sudo certbot certonly --standalone -d <domain>
sudo systemctl start nginx
~~~

如果 80 端口被其他服务占用，请改用 Certbot 的 webroot 或现有证书流程，不要强行停止生产站点。

### 10.2 安装仓库配置

复制配置并编辑两个 server 块中的 server_name，确认证书路径和上游端口：

~~~bash
sudo install -o root -g root -m 0644 \
  deployment/nginx-medchat.conf /etc/nginx/conf.d/medchat.conf
sudoedit /etc/nginx/conf.d/medchat.conf
~~~

至少确认：

~~~nginx
server_name <domain>;
proxy_pass http://127.0.0.1:6001;
proxy_pass http://127.0.0.1:6001/ws;
ssl_certificate /etc/letsencrypt/live/<domain>/fullchain.pem;
ssl_certificate_key /etc/letsencrypt/live/<domain>/privkey.pem;
~~~

location /ws 中的以下配置不能删除，否则聊天流式连接可能失败：

~~~nginx
proxy_http_version 1.1;
proxy_set_header Upgrade $http_upgrade;
proxy_set_header Connection "upgrade";
~~~

检查并加载：

~~~bash
sudo nginx -t
sudo systemctl enable --now nginx
sudo systemctl reload nginx
~~~

### 10.3 防火墙

公网只开放 SSH、HTTP 和 HTTPS：

~~~bash
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status
~~~

不要开放 6001 或 11434 到公网。应用端口和 Ollama 应只监听本机。

## 11. 上线验收

按下面顺序执行，不要只打开首页就宣布部署完成：

~~~bash
curl -fsS https://<domain>/health
sudo systemctl is-active medchat
sudo systemctl is-active nginx
python scripts/health_check.py --env-file /etc/medchat/medchat.env --strict
~~~

浏览器验证：

1. 首页可以通过 HTTPS 打开；
2. 普通聊天可以建立 WebSocket，浏览器开发者工具中没有 ws/wss 错误；
3. 目标搜索能查询已准备的数据；
4. 结构下载、分子属性、反向寻靶、活性预测和对接功能只在对应真实资产存在时验证；
5. 对不可用模型或工具，页面显示明确的不可用/失败状态，而不是分数或成功提示；
6. nginx 日志、systemd 日志和应用日志不包含 API key、Cookie 或完整用户输入。

建议检查上传大小限制是否满足业务需要：仓库 nginx 配置当前为 client_max_body_size 256m，应用自身的上传限制也要一并检查，不能只调大 nginx。

## 12. 更新、回滚和备份

### 12.1 更新代码

在维护窗口执行，并保留当前 commit：

~~~bash
cd /home/user/XIAOKX/molecular_chat_system
git fetch origin
git status --short
git log -1 --oneline
git switch --detach <reviewed-commit-sha>
conda activate medchat
python -m pip install -r deployment/requirements.txt
python -m compileall -q src scripts
python scripts/health_check.py --env-file /etc/medchat/medchat.env --strict
sudo systemctl restart medchat
sudo systemctl reload nginx
~~~

不要在有未提交本地改动时直接覆盖工作树；先保存或清理这些改动，并明确记录变更来源。

### 12.2 备份

至少备份以下内容：

- /etc/medchat/medchat.env，通过受控密钥管理保存，不能进入 Git；
- /home/user/XIAOKX/molecular_chat_system/data/ 和 scratch/ 中的 SQLite、模型元数据、FAISS 索引和结构缓存；
- logs/ 中需要留存的脱敏运维日志；
- 当前代码 commit SHA 和依赖 profile。

备份 API key 时使用密钥管理器或加密备份，不要把明文环境文件放进普通备份目录。

### 12.3 回滚

回滚应切回已验证的代码 commit，再重启服务：

~~~bash
cd /home/user/XIAOKX/molecular_chat_system
git switch --detach <previous-reviewed-commit-sha>
sudo systemctl restart medchat
curl -fsS https://<domain>/health
~~~

数据库 schema、模型 registry 和代码版本必须兼容；只回滚代码、不回滚不兼容的数据资产可能导致启动或科学结果失败。

## 13. 常见故障排查

### Permission denied (publickey)

这是 GitHub 没有接受当前公钥，不是仓库目录权限问题。执行：

~~~bash
ssh-add -l
ssh-add ~/.ssh/id_ed25519
ssh -T -i ~/.ssh/id_ed25519 -o IdentitiesOnly=yes git@github.com
~~~

确认 id_ed25519.pub 已添加到拥有仓库权限的 GitHub 账号；绝不要上传 id_ed25519 私钥。

### systemd 启动后立即退出

~~~bash
sudo journalctl -u medchat -n 200 --no-pager
sudo systemctl cat medchat
~~~

重点检查 Python 路径、WorkingDirectory、环境文件权限、依赖环境和 MEDCHAT_PORT 是否冲突。

### curl 127.0.0.1:6001/health 失败

~~~bash
sudo systemctl status medchat --no-pager
sudo ss -ltnp | grep 6001
sudo journalctl -u medchat -n 100 --no-pager
~~~

先修复应用本身，再排查 nginx；不要通过暴露 6001 到公网来绕过问题。

### nginx 502 Bad Gateway

~~~bash
sudo nginx -t
sudo systemctl status medchat --no-pager
curl -v http://127.0.0.1:6001/health
sudo tail -n 100 /var/log/nginx/error.log
~~~

通常是应用没有监听 6001、systemd 用户无权限，或 nginx proxy_pass 写错。

### 页面能打开但聊天断开

检查 nginx 的 /ws 区块是否保留 Upgrade、Connection "upgrade"、proxy_http_version 1.1，并在浏览器中确认连接使用 wss://<domain>/ws。

### 健康检查缺少科学资产

按 data/REGISTRY.md 准备真实数据库、缓存、权重或索引。缺少资产时保留 unavailable 状态，不要复制样例文件并宣称生产模型已经就绪。

## 14. 部署完成标准

只有同时满足下列条件，才能把本次部署记录为“基础 Web 部署完成”：

- 代码 commit、Python 环境和配置文件均已记录；
- python -m compileall -q src scripts 通过；
- scripts/health_check.py --strict 的失败项已解释，真实启用的功能对应检查通过；
- systemd 重启后应用可恢复；
- nginx -t 通过，HTTPS 和 WebSocket 均已验证；
- 防火墙未暴露 6001、11434 和内部数据库；
- API key、证书私钥、用户数据和运行产物没有进入 Git；
- 备份和回滚路径已经实际验证。

如果启用了 Temporal、OpenSandbox、真实活性模型或真实对接 worker，还必须在目标 Linux 环境完成各自的安装、信任链、重启恢复和真实验收；本教程的单机 systemd 验收不能替代这些专项验收。
