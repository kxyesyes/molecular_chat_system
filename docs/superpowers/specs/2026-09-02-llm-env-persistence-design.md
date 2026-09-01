# LLM API Key 本地 `.env` 持久化设计

## 目标

用户在“模型接入设置”中保存外部模型 API Key 后，刷新页面或重启 MedChat 不需要再次填写。真实 Key 仅写入本机 `.env`；仓库继续提交 `.env.example` 占位模板，不提交 `.env`。

## 文件与优先级

- `.env.example`：Git 跟踪，只包含空值或占位说明。
- `.env`：本地明文运行配置，由 `.gitignore` 排除。
- `.env` 同时保存 `MEDCHAT_LLM_PROVIDER` 与 `MEDCHAT_LLM_STREAM`，作为 UI 配置的重启恢复来源。
- `scratch/llm_runtime_config.json`：继续作为不含 Key 的兼容缓存；当 `.env` 有 provider 标记时不覆盖它。
- 显式部署环境变量仍优先于 `.env`；UI 保存后同步当前进程中的受管变量，以兼容开发热重载。

## 保存行为

- OpenAI-compatible/custom：更新 `OPENAI_COMPATIBLE_API_KEY`、`OPENAI_COMPATIBLE_BASE_URL`、`OPENAI_COMPATIBLE_MODEL`。
- ModelScope：更新 `MODELSCOPE_API_KEY`、`MODELSCOPE_BASE_URL`、`MODELSCOPE_MODEL`。
- Ollama：更新 `OLLAMA_BASE_URL`、`OLLAMA_MODEL`，不写 API Key。
- API Key 输入为空且 provider 未改变：沿用已有 Key。
- `clear_api_key=true`：删除当前 provider 对应的 Key；切换到 Ollama 时也删除此前活动外部 provider 的 Key。
- 保留 `.env` 中所有无关配置与注释；重复目标键会收敛为一行。

## 写入安全与接口

- 拒绝包含换行或 NUL 的值，防止环境变量注入。
- 使用进程内锁、跨进程文件锁、同目录临时文件和 `os.replace()` 原子替换；`.env` 与运行时 JSON 均采用原子写入。
- 跨进程锁位于当前服务用户控制的目录（Windows LocalAppData，Linux XDG runtime 或用户 cache），可用 `MEDCHAT_LLM_LOCK_DIR` 覆盖；Unix 拒绝符号链接锁文件。
- POSIX 上将新文件权限收敛为 `0600`；Windows 依赖当前用户目录 ACL。
- 日志、HTTP 响应和测试输出不包含真实 Key；公共配置仅返回 masked hint 和 `api_key_configured`。
- 外部模型错误响应体属于不可信输入，不写入日志，也不从连接测试接口原样回传。

## 重启恢复

现有 `main.py` 和 `src/web/app.py` 已在启动时加载 `MEDCHAT_ENV_FILE`（默认 `.env`）。保存路由写入同一路径及明确的 provider 标记，因此下一次启动会恢复正确服务商及其 Key；多个服务商 Key 并存时不会串用。环境变量仍可用于服务器部署并拥有更高优先级。

每个 Web worker 会按文件指纹轮询 UI 管理的 `.env`，并在新 WebSocket、配置读取、测试或切换前再次检查，从而使其他 worker 在约 1 秒内应用同一模型配置。后台 watcher 在应用 shutdown 时取消。

## 验收

- 保存新 Key 后 `.env` 包含对应变量，运行时立即启用。
- 再次构造应用时能从 `.env` 恢复 Key。
- 留空保存不清除已有 Key；明确清除会删除 Key。
- `.env.example` 不含真实 Key，`.env` 仍被 Git 忽略。
- 非敏感配置、注释和未知变量不被破坏。
