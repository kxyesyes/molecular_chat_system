# 分子聊天系统 (Molecular Chat System)

## 项目概述

MedChat 是面向药物设计与药物化学研究的 Web 平台，整合自然语言聊天、RAG 检索、分子生成/设计、Vina 对接、反向寻靶、活性与 ADMET 预测。支持文本、表格、分子结构和可下载结果，用于候选探索、筛选及多步骤辅助分析。

项目仍在开发中；分子生成使用自定义微调的 Llama3-8B（Ollama `gmm-llama:latest`），Agent 负责串联工具。科学结果依赖真实模型、数据和工具，缺失时必须明确报告不可用；预测不等于实验结论。

- 使用与部署：[文档索引](docs/README.md)。
- 开发协作：[AGENTS.md](AGENTS.md)；技术栈和目录：[项目规范](docs/PROJECT_STANDARDS.md#2-技术栈说明)。

## 快速开始

### 环境要求

- Python 3.10+
- Ollama 本地服务
- 建议具备 8GB 以上内存

### 安装依赖

```bash
pip install -r requirements.txt
```

### 启动项目

```bash
python main.py
```

应用默认监听 `127.0.0.1:6000`，生产环境通过 nginx 的 HTTPS 域名访问。6000 是浏览器受限端口，直接打开可能出现 `ERR_UNSAFE_PORT`；本机浏览器调试可用 `python main.py --port 6001`，再访问 `http://127.0.0.1:6001`。端口、主机、worker 数等运行参数可通过 `.env` 或命令行覆盖；命令行参数优先级最高。已有 `.env` 和服务器 nginx 配置不会随 Git 更新自动改写，迁移步骤见 [部署教程](docs/DEPLOYMENT_TUTORIAL.md#默认端口从-6001-迁移到-6000)。

聊天运行链路默认保持稳定的 `legacy` 入口。完成决策链验收后，可在启动前显式启用：

```powershell
$env:MEDCHAT_CHAT_PROFILE = "decision_a2"   # 或 semantic_v1
$env:MEDCHAT_DECISION_WIRE_MODE = "native" # 或 json
python main.py
```

未设置 `MEDCHAT_CHAT_PROFILE` 时不会切换入口；发现问题可改回 `legacy`，保留回退路径。

### 主模型接入与一次保存

首页默认使用 DeepSeek 官方 OpenAI 兼容接口 `https://api.deepseek.com/chat/completions`、
`deepseek-v4-pro`、流式输出；API Key 初始为空。在“模型接入设置”中填写自己的 Key 并保存，
同一本机、同一系统账号下重启、更新代码或切换工作树均会继续读取它。
接口/模型依据：[DeepSeek 官方文档](https://api-docs.deepseek.com/zh-cn/)。

- Windows：`%LOCALAPPDATA%/MedChat/config/llm.env`。
- Linux/其他系统：`${XDG_CONFIG_HOME:-~/.config}/medchat/llm.env`。
- 可通过 `MEDCHAT_USER_CONFIG_DIR` 选择仓库外的绝对目录（服务账号也需明确自己的配置目录）。

配置为本机私有明文文件，不上传 GitHub、不在公开 API 中返回 Key。POSIX 目录/文件权限为
0700/0600；Windows 检查目录/文件 owner 和 ACL，不允许不可信账号读写。权限不符会拒绝保存，
请选用当前账号的私有目录，不要降低权限检查。该机制不抵御已控制当前系统账号的进程。

密码框留空只会在相同服务商、相同接口地址时保留已保存 Key；换接口应填写对应的新 Key。
勾选“清除”可移除已保存 Key。保存成功不等于连接成功，请另外点击“测试连接”。
删除配置目录、重装系统、更换电脑或系统账号需要恢复配置；并非云端永久保存。

**兼容变化：** 首页不再隐式读取仓库 `.env`、旧 `scratch/llm_runtime_config.json`、
`MEDCHAT_LLM_CONFIG_PATH` 或 `OPENAI_COMPATIBLE_*` 等主模型环境设置，避免恢复历史接口/密钥。
已有的旧 Key 不自动迁移；首次升级需填写一次新 Key。独立验收 CLI 的显式环境变量接口仍保留，
端口、科学资产及 Ollama 配置不受影响。分子生成工具仍使用本地 `gmm-llama:latest`。

## 项目结构

从 `main.py` 启动；Web 入口在 `src/web/`，Agent 和领域工具在 `src/` 各模块。配置、数据、测试分别位于 `config/`、`data/`、`tests/`。

完整职责表见 [项目规范](docs/PROJECT_STANDARDS.md#3-目录结构说明)，资产清单见 [data/REGISTRY.md](data/REGISTRY.md)。

## 许可证

本项目采用 MIT 许可证。
