# Agent 动态执行会话集成

日期：2026-09-12。分支 `codex/agent-dynamic-session-integration`。
状态：实施、独立规格及质量复审通过，准备独立 draft PR；CI 尚待运行。
创建时依赖 PR #22 的 `f4337ef`，随后快进到测试修订 `8ae557f`。
PR #22 已获具体授权 squash 为 `57c677e`；父任务确认整树与 `8ae557f` 相同后正常合并 main 祖先，不覆盖工作区。
本批单独提交动态 session，不切换生产 Agent。

## 本批范围

复用 `WorkflowRunSession`，选择性移植来源 `9312bf5` 的动态会话增量，保留静态模式兼容。
动态会话接受已通过上层授权的单次决策，不生成固定步骤表；逐步执行已有 adapter/validator/persistence/event 链路。
覆盖独占启动、一步结算后追加下一步、必需步骤失败停止、可选步骤失败、实际输出摘要、失败结果留存与最终状态。
恢复接口只装载上层已验证并原子领取的历史观察；本批不实现用户认证、checksum/revision 校验或崩溃恢复。
相关实现为 `src/agent/runtime/run_session.py`，新增测试为 `tests/agent/test_dynamic_run_session.py`。
原始混杂树、PR #22 分支、生产服务和模型保持不变。

## 后续 harness 集成必须保留的边界

- 来源 `decision_loop.py` 使用 LangGraph 和逐轮模型提案，最多 16 次模型请求、12 次工具尝试、300 秒；不误报为全部科研工具已接线。
- 来源初始目录只有 property、drug likeness、activity、target search 四类只读工具；其他历史科研能力与最终入口仍需单独验证。
- `decision_continuation.py` 的协议 revision 3、用户/会话/configuration/checksum/完整历史和计数校验必须实际移植；PR #22 的 CAS 不能替代这些校验。
- `decision_inputs.py` 核对数据摘要、工具/状态/provenance/evidence/artifacts/输入绑定，并保留用户 target 续接。声明“使用上游输出”必须以实际工具输入证明。
- 新凭据 guard 要求调用方先限制输入容器/文本；不得把原始 source 的无界入口当作已验证安全。
- 成功返回工具数据不等于最终回答可信；科学回答仍需上层服务端证据门禁，外部模型文本不能直接冒充计算结果。

## 验证与审查

- 初始缺少动态 API，47 项失败；最小实现后动态 55 项通过。输入绑定失败路径的 adapter version 曾被错误覆盖，已复现修复。
- 规格审查另发现：success/SUCCEEDED 工具观察仍可带结构化 error，原完成门禁遗漏聚合 result.error。
  正式默认/显式 COMPLETED 两项先因未抛错失败，加入 result.error 检查后动态 57 passed；拒绝后可 PARTIAL 终结并保留错误。
- 独立规格复审：57 项及原独立探针合计 59 passed；扩展聚焦 233 passed。无未解决规格意见。
- 独立质量初轮：214 passed；探针10 passed、1 failed，定位调用方错误对象未隔离，持久化首失败后可被改写而与已落库事件不一致。
  正式嵌套 details 回归先1 failed；最小深拷贝选定错误后动态58 passed，保留错误内容与已有完成门禁。
  质量复审原探针11 passed、聚焦215 passed，无未解决质量意见；父最终联合 **2744 passed、1 skipped、7 warnings，56.42s**。
- 来源 continuation/空聊天原版 24 passed；替换本批 session 后 23 passed、1 failed：用户修正原无效输入后，旧 loop 强求 COMPLETED，且异常兜底再次 finish 未终结会话。
  后续 harness 必须对齐保留历史失败的 PARTIAL 语义和异常兜底，不能放宽本批科研完成门禁；这是来源兼容缺口，不是已修复功能。
- 依赖修订前全 Agent 曾触发已知 near_budget 矩阵超时；不以重跑通过声称修复。依赖 PR #22 修订后，父联合回归：
  `python -B -m pytest tests/agent tests/test_agent_decision_model.py tests/test_openai_compatible_model.py tests/test_agent_anti_hallucination_fallbacks.py tests/test_agent_platform_health_check.py -q -p no:cacheprovider --tb=short`
  **2743 passed、1 skipped、7 warnings，55.51s**。skip 为 opt-in shadow 性能测试；警告为既有 SWIG/FastAPI 弃用提示。
- 仅合成工具、临时 SQLite 与离线 source 兼容探针；不是外部模型或真实科研验收。
- `compileall src scripts`（缓存置系统临时目录）、8 个 Node 契约脚本、`run_agent_acceptance.py --mode contract`、diff-check 均通过。无效 SMILES 的 RDKit 解析诊断为拒绝用例预期输出。

## 下一批已定位的来源迁移门禁

1. `harness/decision_policy.py:64` 的 `usable` 不检查结构化 error。只读合成探针返回 `source_usable_with_error: true`；未来移植必须补红绿测试，不能把它作为科学回答/下游输入的可信门禁。
2. `decision_loop.py:101` 在约束原始文本前运行凭据扫描；`decision_policy.py:26` 和 `decision_continuation.py:59` 先完整序列化再检查字节预算。需复用现有有界 JSON 工具或等价早拒绝，避免撤销 PR #21/#22 的资源边界。
3. `decision_loop.py:410–414` 的 finish 异常兜底只适用于已缓存终结结果的幂等持久化重试，不适用于 session 生命周期拒绝；修正输入恢复的 24 项来源对照必须纳入下一批。

以上为未进入 main 的来源迁移问题；本批只加 session 门禁，不声称 loop、授权、完整性和 continuation 调用层已完成。

独立双审完成，最终 CI 待 PR 创建后运行。整体历史集成仍为 partial。
