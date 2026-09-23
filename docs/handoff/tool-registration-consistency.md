# T04：工具注册与执行归属一致性

日期：2026-09-20；分支 `codex/tool-registration-consistency`；基线 `d89a48b`。
工作树：`D:/MedChat/molecular_chat_system_worktrees/tool-registration-consistency`。

## 范围与复现

已阅读任务书 T04、仓库 AGENTS/PROJECT_STANDARDS、T02 交接、应用工厂、工作流 API、
Supervisor、Specialist、工具注册/适配、能力与工作流目录和相关测试。
本地 `main` 仍为旧指针 `3b87853`，未移动它；沿用前三批的核验基线。
原始混杂工作树不动；T01/T02/T03 仍独立、未集成。

首轮 7 项测试失败，复现：

- 真正的 `MolecularChatApp._create_chat_agent()` → `_create_supervisor_agent()` →
  工作流 POST handler → Supervisor.run 委派，RAG 因缺 `rag` 执行者失败。
- 先注册别名再注册同名规范工具，冲突未拒绝；其他冲突虽抛异常但残留半注册工具。
- 未知工具被工厂无提示丢弃；默认 RAG 规范名/别名不能获得执行者。

API 测试只替换外部 embedding、FAISS 索引数据和后台调度边界，不手工补齐 Supervisor。
使用真实 RAG 工具、适配器、默认执行者、规划、事件和临时 SQLite。测试数据为合成离线样例，
**不代表真实数据库、模型或科学预测验收**。

## 修改与行为

- 增加无 LLM 的 `RAGAgent` 执行描述，复用既有 Specialist，不另建推理循环。
- 规范化 `rag_database_search` 为 `rag_search`，别名保留；冲突检查全部完成后才写入注册表。
  规范名、别名、相互碰撞和自别名都明确拒绝。
- 工厂未知拥有者明确报错；既有 `rxn_chemistry_agent` 作为明确声明并记录日志的
  legacy chat-only 排除项，不冒充工作流能力，也未为其扩展权限。
- 适配器保留旧领域标签，同时索引正式能力 ID，如 `molecule.activity`。
- `audit_registration(registry, specialists)` 检查拥有者、白名单、工作流引用、必需工具、
  能力索引及别名实例身份。应用委派工厂自动执行预检查，错误抛出而不是伪称能力可用。
  报告保存在 `app.agent_registration_report`；可随本地状态变化重新执行，无模型或网络探测。
- 默认委派工厂复用现有注册表，显式不再构建第二套工具池；聊天工厂将本应用 RAG 实例注入工具。

## 健康状态约定（兼容注意）

注册成功不等于科学模型已加载。本批为惰性工具的 `health().available` 增加 `null` 状态：

| available | 含义 | 调度行为 |
|---|---|---|
| `null` | `not_probed`，运行依赖未检查 | 允许按请求惰性尝试，不能当作科学成功 |
| `false` | 已明确禁用或本地条件不满足 | 拒绝并保留 unavailable 错误 |
| `true` | 适配器或明确探测范围就绪 | 不保证科学结果；仍须真实执行/校验 |

RAG 的 true 仅表示本地初始化标志与索引就绪，**不证明 embedding 服务在线或 T01 行映射校验通过**。
注册表名称/能力解析只拒绝明确 false；决策模型目录保留 JSON null 并解释其含义。
能力预检查不探测外部服务，使用 `not_probed` 或 `unavailable`，不假报真实模型就绪。
调用后的 `last_execution_status`、`last_error_code` 只记录枚举状态/错误码，不保存异常原文、路径或输入。
单次失败不会全局禁用共享工具，单次成功也不会证明所有家族/靶点均可用。

健康返回的 null 是需要后续消费者注意的语义扩展，不是改变科学结果成功/失败判定。
本批没有启用模型、扫描用户权重或训练资产、读取密钥、改变科学验证器、删除错误/来源或移除资源清理。

## 修改文件

- `src/agent/capabilities/catalog.py`
- `src/agent/harness/decision_policy.py`
- `src/agent/specialists/__init__.py`
- `src/agent/specialists/agents.py`
- `src/agent/tooling/adapters.py`
- `src/agent/tooling/factory.py`
- `src/agent/tooling/registry.py`
- `src/agent/tooling/registration.py`（新增）
- `src/agent/tools/rag_search_tool.py`
- `src/web/app.py`
- `tests/agent/test_registration_consistency.py`（新增）
- `tests/agent/test_specialist_agents.py`（正式能力 ID 的断言）
- `tests/agent/test_task_planner.py`（别名存在测试不假定未初始化 RAG 可用）
- `docs/handoff/tool-registration-consistency.md`、`docs/handoff/latest.md`

## 实际验证

解释器为 `C:/Users/xkx52/.conda/envs/MedChat/python.exe`，所有 pytest 使用
`-B -m pytest ... -q -p no:cacheprovider --tb=short`。

- 首轮 RED：7 failed；随后增加预检查/可选状态用例；曾纠正测试替身的 AgentTask 字段与 predictor 方法名，不将替身错误计为产品缺陷。
- 中间联合回归：3314 passed、2 skipped、7 warnings（尚未加入最终三态健康与补充测试）。
- 当前聚焦：`tests/agent/test_registration_consistency.py tests/agent/test_tool_registry.py tests/agent/test_specialist_agents.py tests/agent/test_task_planner.py`：269 passed。
- `-m compileall -q src scripts`、`git diff --check`：通过。
- `node tests/home_agent_task_panel_test.js`：通过。
- 独立只读复审指出的别名实例一致性与未探测依赖假就绪问题均已补 RED/GREEN 测试并修复，最后复审无阻断项。
- 最终联合回归：**3325 passed、2 skipped、7 warnings，123.24 秒，exit 0**。范围为 `tests/agent tests/test_phase2_phase3_routes.py tests/test_agent_anti_hallucination_fallbacks.py tests/test_agent_platform_health_check.py`，另加 `-rs`；包含本批 37 项新增回归。
  两项跳过为当前环境不支持目录符号链接、默认关闭的性能测试；7 项为既有 SWIG/FastAPI 弃用警告。
  最终联合回归之后未再修改业务代码或测试。

## 未完成与下一批

- 未提交、推送、创建 PR、合并、部署或重启；未调用真实外部模型/预测器/Vina。
- T01 的 RAG 行映射、T02 的终态/检查点、T03 的提示预算仍在各自工作树，本批不声称包含这些修复。
  集成时需协调 T01 的 RAG 实例注入/检索校验，联合验证 T02 委派结果状态；本批未迁移共享 Session（T07）。
- 下一批建议 T05：先核验 T05-A 在当前基线是否已解决（当前只见统一主模型构造，不重复改），
  再复现并修复 T05-B 分子设计服务切换主模型后的生命周期/引用一致性。
