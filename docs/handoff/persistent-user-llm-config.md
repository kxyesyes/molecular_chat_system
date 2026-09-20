# 主模型默认值与跨工作树配置持久化

日期：2026-09-20；分支：`codex/persistent-user-llm-config`；基线：`20e6f44`。

## 行为与兼容边界

- 首页主模型默认为 DeepSeek 官方 `https://api.deepseek.com/chat/completions`、
  `deepseek-v4-pro`、流式输出、空 Key。
- UI 保存到 Windows `%LOCALAPPDATA%/MedChat/config/llm.env`，Linux 使用
  `${XDG_CONFIG_HOME:-~/.config}/medchat/llm.env`；允许仓库外绝对目录覆盖。
- 同一机器/系统账号的工作树共享配置；更新代码不会覆盖它。删除配置目录、重装系统或
  更换机器不在此保证范围内。文件是受权限保护的明文，不是加密保险库。
- 不自动迁移旧 Key，不从仓库 `.env`、旧 runtime JSON 或环境变量补回 UI Key。
  独立验收 CLI 的环境变量配置接口不变；Ollama 分子生成模型不变。
- 同 provider/规范化端点留空保留 Key；清除或改变端点不会借用旧 Key。
  保存与连接验证分开，公开配置只含是否配置与固定掩码。
- 保存采用已有跨进程锁/原子替换；损坏/不可信配置拒绝访问，包括已经连接的聊天窗口。
- `retire_legacy_ui_llm_config` 仅由操作者显式用于一个指定运行目录；不扫描其他工作树，
  保留科学工具/服务字段，不生成旧密钥备份。

## 修改范围

核心：`src/web/user_llm_config.py`、`src/web/llm_runtime_config.py`、
`src/web/app.py`、`src/web/chat_handler.py`。
UI：`src/web/static/js/home/main.js`、`src/web/templates/index.html`。
文档：`.env.example`、`README.md`、本交接和本批设计/实施计划。
测试：`tests/conftest.py`、三个 `test_user_llm_*` 模块、
`tests/test_llm_runtime_config.py`、`tests/test_admin_auth_routes.py`、
`tests/home_llm_settings_test.js`。

## 验证

使用 MedChat Conda Python；所有配置测试使用合成 Key 和临时目录。
pytest 在 collection 之前及每个测试开始时隔离用户配置，不接触真实用户凭据。

- 聚焦配置/路由/鉴权历史回归：78 passed、3 skipped。
- 联合 `tests/agent`、配置、路由、接线、模型适配与部署资产回归：3365 passed、5 skipped。
- 反幻觉、平台健康和分子生成器补充回归：56 passed、162 subtests passed。
  Windows 跳过项为 symlink 权限、POSIX 权限模式和 Linux-only shadow 测试。
- Node 设置行为：14/14；前端静态 XSS、任务面板检查通过。
- `node --check src/web/static/js/home/main.js`、`python -m compileall -q src scripts`、
  `git diff --check` 通过。
- 独立规格审查发现旧 socket 配置刷新、重启 streaming、collection 隔离三个问题；
  新增测试先得到 3 failed，再修复为通过；规格复审通过。
- 质量审查复现空格模型名/特殊换行导致不可重读的文件；补充 5 个先失败后通过的
  测试，在规范化之前拒绝所有行分隔符，拒绝空模型名，并验证原文件保持不变。
- 最终独立规格、质量审查均批准；质量复查 32 passed、2 skipped，原始复现探针通过。

实际命令（均附 `-q -p no:cacheprovider --tb=short`）：

```text
python -B -m pytest tests/agent tests/test_user_llm_config.py tests/test_user_llm_routes.py tests/test_user_llm_test_isolation.py tests/test_llm_runtime_config.py tests/test_admin_auth_routes.py tests/test_agent_llm_wiring.py tests/test_openai_compatible_model.py tests/test_deployment_assets.py
python -B -m pytest tests/test_agent_anti_hallucination_fallbacks.py tests/test_agent_platform_health_check.py tests/test_llm_molecular_generator.py
node tests/home_llm_settings_test.js
node tests/frontend_safe_render_test.js
node tests/home_agent_task_panel_test.js
```

本批不使用任何聊天历史中的真实 Key，不执行付费外部模型调用，不训练/激活模型权重。
本地配置/页面通过不等于 DeepSeek 真实连通成功；需用户填写有效 Key 后测试。
PR 与本地启动状态分别记录，不把本地试运行描述成 main 已合并。

## 交付与本地生效

实现提交 `f7bfae7`；[Draft PR #32](https://github.com/kxyesyes/molecular_chat_system/pull/32)
目标为 main，未合并，CI 最终状态仍待 PR 检查。

经本地回归和独立双审后，现有 `main-runtime` 切换到该提交的 detached 本地预览；
只清理其 `.env` 的旧主模型字段与指定旧 runtime 缓存，未修改其他工作树或科学配置。
原始混杂工作树未更改。旧进程 22288 已停止，新进程 42420 监听 loopback 6001。

实际 HTTP 验证：首页 200、`/health` 为 ok、`/api/llm/config` 为
`openai_compatible` / `https://api.deepseek.com/chat/completions` /
`deepseek-v4-pro` / stream=true / API key present=false。
用户配置文件尚不存在（默认值生效）；等待用户自己在页面填入 Key 并保存。
日志前缀为 `logs/start-20260920-170635`，仅本机运行产物，不提交。
