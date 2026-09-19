# 部署前隔离验收（2026-09-20）

## 结论与范围

总体为 **partial：本地工程检查部分通过，尚不能宣称可部署到公网**。
基线为 PR #30 的 squash 提交 `cd041120c85e49a7de01a789430635f98a660bdc`，
验收分支为 `codex/deployment-preflight`。本批仅新增/更新交接文档，没有业务代码改动。

PR #30 已获授权合并；合并树与审查 head `6824137` 相同。该 head 的 CI run
`35452324814` 七项通过属于合并证据，不是本轮重新执行的测试。
下方旧交接中的“未合并”只描述当时状态。

本轮在新建独立工作树执行，未覆盖原始混杂工作树，未读取真实密钥，未调用外部主模型，
未训练、复制或激活真实权重，未重启现有服务，未修改 ACL、生产鉴权或部署配置。
健康检查可能在隔离工作树内初始化本地运行目录/元数据；不是对生产数据的检查。

## 当前部署边界

- `src/web/app.py` 仍构建 `SupervisorAgent`；合并新的逐轮决策 harness 不等于生产首页已切换。
- `src/web/decision_lab.py` 是独立 loopback 验收入口，不是公网认证方案。
  `src/web/chat_handler.py` 中另有显式 decision message 接口，不能由此推断首页默认使用它。
- 管理接口无需管理员令牌是现有行为，见 `tests/test_admin_auth_routes.py`。
  `deployment/nginx-medchat.conf` 的示例将 HTTP `/` 和 `/ws` 代理到应用，未提供 TLS/访问控制。
  本轮没有擅自恢复用户此前移除的鉴权；公网开放前必须另行确定访问边界。
- `deployment/medchat.service` 使用 Linux 的 `/opt`、`/etc` 路径、medchat 服务账号和 Conda Python。
  Windows 上文件检查通过不代表目标机 systemd、权限、证书或依赖已通过。
- 原始工作树报告展示等历史残差仍需逐项核对，不能整体合并；本批没有处理或宣称清零。

## 本轮实际结果

| 检查 | 结果 | 证据边界 |
|---|---|---|
| 部署、健康检查、聊天桥接、隔离验收、管理接口聚焦 pytest | 417 passed / 84 skipped / 7 warnings，39.96 秒 | Windows 本地工程测试，不是服务器上线验收 |
| OpenSandbox 静态校验 | passed，exit 0 | 不证明 Docker、隔离网络、真实 Vina 作业运行成功 |
| Temporal 静态校验计算 | partial | Python 静态检查通过；Docker、promtool 不可用，systemd 检查需 Linux |
| Temporal CLI 报告发布 | failed，exit 1 | 报告目录 DACL 未通过可信写入检查，没有生成报告文件 |
| 健康检查 `--strict` | 19/23 passed，exit 1 | 新工作树缺少下述运行资产和目录 |
| Agent contract | 34/34 passed，exit 0 | 路由/计划/契约，不是真实模型或科研全链路 |
| `decision_lab_ui_test.js` | 30/30 passed | Node UI 回归 |
| `activity_family_results_test.js` | 22/22 passed | Node 家族结果展示回归 |
| `compileall` | exit 0 | Python 编译检查 |

84 个 skip 包括 Linux/POSIX、权限/root/systemd/firewall、符号链接能力、缺少 Docker/promtool、
未 opt-in 特权 metrics relay，以及被后续覆盖替代的旧测试。它们不计为通过。
警告包括 FAISS SWIG 和 FastAPI `on_event` 弃用提示。

健康检查找到 RDKit、Vina、ADFRsuite、配体准备工具；Ollama tags 真实可达且安装了
`gmm-llama:latest`。这是依赖发现/连通性证据，没有执行分子生成或真实 docking。
配体准备工具位于另一 Python 安装中，运行兼容性仍待真实作业确认。
新工作树未配置 `data/reverse_target`、`data/activity/models`、
`data/molecular_faiss_index.index`，且缺 `scratch` 目录。
这些失败不能解释成原始工作树或其他工作树的资产丢失。
“TemporalRuntime local”检查通过只表示默认本地后端，不表示 Temporal 服务已部署。

### Temporal 报告发布故障定位

同一 CLI 使用相对及绝对输出路径均失败。直接调用 `validate_deployment()` 得到 partial，
故障发生在 `write_report_atomic()` → `secure_io.write_json_atomic()` →
`trusted_files.capture_trusted_path_boundary()`，错误为 `unsafe report output`。

只读检查输出布尔状态：父目录存在、路径身份检查通过、owner 可信、`dacl_trusted=false`、
目标报告不存在。因此问题是报告目录权限边界，不是验证逻辑完全无法在 Windows 执行。
未放宽安全写入器或修改目录权限。下一步应由部署维护者确定专用报告目录及最小权限，
按其部署权限方案配置后重跑；不得改用普通写文件绕过安全门并声称 CLI 通过。

## 可复现命令与产物

以下命令在独立工作树根目录运行。`python` 表示具备 RDKit/PyTorch 依赖的 MedChat Conda Python；
具体机器绝对路径不写入可提交配置。

```powershell
python -B -m pytest tests/test_deployment_assets.py tests/test_temporal_deployment_assets.py tests/test_temporal_deployment_integration.py tests/sandbox_broker/test_deployment_assets.py tests/test_agent_platform_health_check.py tests/agent/test_decision_chat.py tests/agent/test_decision_chat_transport.py tests/agent/test_decision_chat_acceptance.py tests/agent/test_decision_lab.py tests/agent/test_decision_lab_cli.py tests/agent/test_decision_lab_lifecycle.py tests/test_admin_auth_routes.py -q -p no:cacheprovider --tb=short -rs --junitxml=outputs/deployment_preflight/focused.xml
python -B scripts/validate_opensandbox_deployment.py --static
python -B scripts/validate_temporal_deployment.py --output outputs/deployment_preflight/temporal-static.json
python -B -c "import json; from scripts.validate_temporal_deployment import validate_deployment; print(json.dumps(validate_deployment()))"
python -B scripts/run_agent_acceptance.py --mode contract --output outputs/deployment_preflight/agent-contract.json
node tests/decision_lab_ui_test.js
node tests/activity_family_results_test.js
python -m compileall -q src scripts
```

健康检查特意使用环境白名单子进程，不继承生产资产路径或凭据、不加载实际 `.env`：

```powershell
python -B -c "import os,subprocess,sys; keys=('SystemRoot','WINDIR','PATH','PATHEXT','SYSTEMDRIVE','TEMP','TMP'); env={k:os.environ[k] for k in keys if k in os.environ}; env.update(PYTHONIOENCODING='utf-8',PYTHONDONTWRITEBYTECODE='1',MEDCHAT_TASK_BACKEND='local',OLLAMA_BASE_URL='http://127.0.0.1:11434',MOLECULAR_GENERATOR_MODEL='gmm-llama:latest'); result=subprocess.run([sys.executable,'-B','scripts/health_check.py','--strict','--env-file','outputs/deployment_preflight/not-loaded.env'],env=env,timeout=120); sys.exit(result.returncode)"
```

`not-loaded.env` 是本轮不存在的路径，不创建该文件或向其中写入凭据。
仅在同样的隔离环境中复现健康检查，不将以上命令直接用于生产目录。

本地忽略产物：`outputs/deployment_preflight/focused.xml`、
`outputs/deployment_preflight/agent-contract.json`；不提交到 Git。
`temporal-static.json` 未生成，不可将它作为已有报告引用。

未运行 `run_temporal_production_preflight.py`：它会启动真实 canary/重复作业并涉及监控、备份，
超出本轮静态与隔离本地检查范围。未运行新的真实权重/外部模型验收，也未测试负载或公网访问。
先前真实 PDE/BuChE 权重验收见 `family-prediction-review.md`，不能算作本轮重测。

## 下一阶段与待确认事项

1. 确认部署目标（本机 Windows 或 Linux）、资源与网络边界；系统/CPU/GPU/内存/域名/HTTPS均待确认。
2. 为目标机制定运行资产挂载、专用报告目录最小权限和访问控制方案，再配置并重跑健康/部署检查。
   不把本地验收页面直接暴露到公网，不提交真实数据、权重或密钥。
3. 在目标环境执行原生 Linux/systemd/Docker/promtool/隔离网络/备份恢复检查；明确生产入口是否迁移，
   再申请真实主模型、生成、活性与 docking 的有界验收及并发验收。

本轮已核对关键文件：`AGENTS.md`、`docs/PROJECT_STANDARDS.md`、
`docs/handoff/latest.md`、`docs/handoff/historical-integration-status.md`、
`docs/handoff/isolated-decision-lab-integration.md`、`docs/handoff/family-prediction-review.md`、
`deployment/README.md`、`deployment/medchat.service`、`deployment/nginx-medchat.conf`、
`main.py`、`src/web/app.py`、`src/web/chat_handler.py`、`src/web/decision_lab.py`、
`scripts/health_check.py`、`scripts/validate_temporal_deployment.py`、
`scripts/run_temporal_production_preflight.py`、`scripts/run_decision_chat_acceptance.py`、
`src/task_runtime/secure_io.py`、`src/task_runtime/trusted_files.py`及上述聚焦测试。
