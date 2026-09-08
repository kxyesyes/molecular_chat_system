# Agent 隔离真实模型验收与有界审查

日期：2026-09-08。分支：`codex/agent-real-model-audit`，起点：`af55114`。

## 结论与边界

已完成真实 PDE/BuChE 基线权重的隔离接入验收，以及六类已复现问题的修复。全量软件测试无失败，但不代表模型预测质量已达到部署标准，也不代表所有外部服务与 Linux 部署均已验收。

真实权重仅复制到 pytest 临时目录并在副本注册表选择；测试前后校验原注册表 SHA-256 不变。未启用生产模型、未重启现有服务、未训练或复用 held-out test 调参、未调用外部主模型、未读取或保存 API key。原项目混杂工作树未修改。

本次使用系统化调试与 TDD：先建立失败回归，再做最小修复；独立审查再次验证状态库归属、兼容行为与验收超时。不是逐行证明整个仓库无 Bug，也没有按行数盲删兼容模块。

## 六类修复

| 问题 | 修复与证据 | 主要文件 |
|---|---|---|
| 自然语言中的 pIC50 被当作 SMILES | 仅从未标注的 prose token 中排除有限指标词；明确 SMILES 字段和畸形结构仍拒绝 | `src/agent/tools/activity_input.py` |
| 通用 Workflow failed 遮蔽真实输入错误 | Supervisor 外层保留 status/error/warnings；Web 优先安全的具体失败原因，保留脱敏与长度限制 | `src/agent/supervisor.py`、`src/web/chat_handler.py` |
| 相同步骤 ID 可复用另一工具的旧 checkpoint | 匹配 tool/model/adapter/workflow/input 身份；普通与 delegated 路径复用同一判断；读写同一状态库 | `src/agent/orchestrators/workflow.py`、`src/agent/supervisor.py` |
| checkpoint 恢复丢失 artifacts | 校验并反序列化 artifact；老记录缺字段兼容为空，畸形记录重新执行并记录警告 | `src/agent/contracts/domain.py`、`src/agent/orchestrators/workflow.py` |
| delegated required 步骤失败仍继续 | 共享步骤失败策略：required 停止，optional 默认继续，显式 false 停止；缺 specialist 同样适用 | `src/agent/orchestrators/base.py`、`src/agent/runtime/run_session.py`、`src/agent/supervisor.py` |
| Router abstain 后重复路由 | execute 仅调用一次既有策略解析；保留显式 skill 和输入预校验 | `src/agent/supervisor.py` |

审查还发现并修正了实施中出现的跨状态库回归；增加无库/异库注入测试。真实验收子进程设置 120 秒 deadline，避免 socket 或推理挂起无限阻塞。

## 真实验收覆盖

新增 `tests/test_activity_family_real_acceptance.py`，覆盖 PDE5A 与 BuChE 两个实际权重案例：

- 真实分类/回归推理，HTTP → 工具 → Supervisor 数值一致。
- 记录两个模型的 provenance，检查 demo_mode=false、fallback_used=false，验证权重可解析。
- ChatHandler 的真实 ASGI WebSocket 流包含终结响应，工具结果不交给主模型重写。
- 将实际 HTTP 结果送入生产 JavaScript renderer，校验 DOM 中类别、数值及模型 ID。
- 无效输入和未知靶点明确失败；原注册表哈希保持不变。

这是实际权重 + ASGI + Node DOM 集成测试，不是完整生产浏览器人工验收。CCO/CCN 冒烟输入不构成实验测量或模型性能评估。一次性隔离探针还检查了 PDE3B/BChE 别名、AChE 拒绝及有效/无效混合批次的 partial 行为。

原基线模型性能与不足仍以 [训练报告](activity_family_baseline_20260908.md) 为准；软件链路通过不改变该结论。

## 实际验证

以下 Python 命令均在具备 RDKit/PyTorch 的 MedChat Conda 环境、当前独立工作树执行；不是系统默认 Python。

| 命令 | 最终结果 |
|---|---|
| `python -B -m pytest tests -q -rs -p no:cacheprovider --tb=short` | 7074 passed，240 skipped，9 warnings，171 subtests passed，435.35 秒 |
| `python -B -m pytest tests/test_activity_family_real_acceptance.py -q -p no:cacheprovider --tb=short`，设置下述资产目录变量 | 2 passed，17.77 秒 |
| `python -B scripts/run_agent_acceptance.py --mode contract --output outputs/agent_evaluation/agent-real-model-audit-contract-final.json` | passed，34 cases |
| `node tests/activity_family_results_test.js` | passed |
| `node tests/activity_prediction_safe_render_test.js` | passed |
| `node tests/home_agent_task_panel_test.js` | passed |
| `node tests/home_workflow_completion_behavior_test.js` | passed |
| `python -m compileall -q src scripts` | exit 0 |
| `git diff --check` | exit 0 |

真实权重验收显式启用方式：

```powershell
$env:MEDCHAT_FAMILY_ACCEPTANCE_MODELS_DIR = (Resolve-Path data/activity/models/baseline-20260908-v1).Path
python -B -m pytest tests/test_activity_family_real_acceptance.py -q -p no:cacheprovider --tb=short
```

未设置资产变量时两项验收跳过，因此全量套件中的跳过不是实际权重验收失败；本轮已单独启用并通过。其他跳过主要涉及 POSIX socket/fcntl、Windows 符号链接权限、Linux root/systemd/防火墙与未启用的性能或部署集成测试。它们没有被计为通过。9 条警告涉及既有 SWIG/FastAPI/PyG 弃用提示和 tensor 构建性能提示，未以无关依赖升级消除。

基线全量为 7042 passed / 238 skipped；最终新增 32 个通过及 2 个 opt-in 跳过。旧基线运行结果不替代最终验证。

## 有界清理

七个生产文件共增加 73 行、删除 115 行，净减少 42 行。清理的是重复路由、重复失败决策和不再被引用的私有持久化函数；没有移除公共 ReAct/legacy 接口或新增另一套框架。新增测试行数不计入生产代码净变化。

仍有大型历史模块（例如 Supervisor、generation_request、evaluation/scientific）。本轮不凭体积拆分；后续应针对单一职责与稳定接口分小 PR 处理，先建立覆盖再迁移。

## 下一步

1. 单独决定模型启用/部署与回滚方案；本轮没有授权或执行上线。
2. 在 Linux 环境补跑本轮 Windows 跳过的生产部署/沙盒/任务运行时检查。
3. 本分支起点已继承相对 main 的 33 个历史提交；先整理依赖链或将本任务提交迁移到合适基线，不直接创建混入所有历史工作的巨型 PR。
