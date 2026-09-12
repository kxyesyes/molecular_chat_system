# 逐轮模型决策 harness 历史集成

日期：2026-09-12。分支 `codex/agent-decision-harness-integration`。
创建时基于 PR #24 提交 `1cb1348`；随后用户明确授权 squash 为 main `6aa5299`。
父任务核对两者整树一致后，普通合并 main 祖先；随后对齐main 2a0fe75，不覆盖本批实施文件。当前仅实施中，无本批 commit/PR。

## 范围与不可替代的目标

选择性移植来源 `9312bf5` 的七个 `src/agent/harness/decision_*.py` 模块：loop、policy、inputs、execution、clarification、requirements、continuation。
复用 LangGraph 每轮模型提案、已有模型传输、工具注册/校验、证据 ledger 和 SQLite 原子续接；不恢复静态 Planner 或重新造执行器。
包括真实输入引用、服务端工具授权、确定性成果验收、模型/工具预算、取消结算、明确失败和 owner-bound 澄清续接。
初始目录仍只有 property、drug-likeness、activity、target-search 四类只读工具；这是来源能力边界，不声称全部历史科研能力完成。

必要公共兼容接点：`ToolAdapter.execute(..., *, allow_retry=True)`；默认保留旧重试行为，显式 False 只尝试一次，类型需严格校验。
工具尝试不能因外层截止或持久化重试而隐式重跑。不得覆盖 main 的其他 adapter 保护。
另一个来源独有接点是 `utils/validators.py` 将解析器抛异常标为 unavailable，而非误诊结构 invalid；需以红绿回归迁移，保持其他完整输入保护。

## 已证实的迁移问题

- 来源 usable 未检查结构化 error，离线探针确认误判；必须拒绝作科学依据，不清空错误。
- 来源 loop 修正无效输入后要求 COMPLETED，与保留旧失败的动态 session 门禁冲突；调用层应如实 PARTIAL，不放宽 session。
- 完成阶段生命周期拒绝不能用普通 `finish()` 冒充幂等持久化重试。
- 源码在原始 context 深拷贝/凭据扫描、观察和快照完整 JSON 序列化之后才校验预算；需先检查有界 plain JSON，再复制、扫描、编码，不截断。
- source 去掉的 workflow 旧持久化方法不是本批待删项，保留 main 兼容接口。

## 后续 Task6 接点（只读盘点，尚未实施）

- `src/web/decision_chat.py` 及 ChatHandler 的 server-only `process_decision_message`：隔离队列、取消工具结算、最终结构化结果；不能把客户端 kwargs 当权限。
- `_sanitize_agent_event_keys` 需可配置展示深度/条数，默认保持原值；限流、队列总量和结构化 warning 类型仍需实际验证。
- `src/web/decision_lab.py`、`src/web/static/decision_lab/{app.js,index.html,style.css}`、两份 run_decision 脚本及对应测试：loopback-only 验收入口，不接管生产主页。
- `tests/test_activity_family_real_acceptance.py`：真实权重 opt-in，只在复制的临时注册表选择模型，验证原 registry hash 不变；依赖 API/Agent/UI 完整集成后再迁移验证，不读取真实训练资产作为普通 CI。
- 原始工作树 presenter 与完整 SMILES 残差、最终完整回归和部署防护仍待处理，不能仅凭七模块通过宣布总体完成。

## 验证约束

先导入来源相关测试观察缺模块失败，再最小移植；新增安全/兼容缺陷分别建立红绿测试。
使用 MedChat Python、`-B -m pytest -p no:cacheprovider`，仅合成模型/工具和临时 SQLite；不读取密钥、CSV、权重或启用生产服务。
## 实施结果（待独立审查）

worker 已完成七模块及 `decision_bounds.py` 有界 plain JSON 辅助模块、10 份新增契约测试和两个获准的兼容接点。
RED：缺模块3个收集错误；初始移植56 failed/140 passed；adapter9 failed/1 passed；迁移门禁37 failed/23 passed；解析器异常分类2 failed。
本批288 passed；全 Agent 及4份顶层相关测试3032 passed、1 skipped、7 warnings，compileall/diff-check通过（worker报告，父独立回归待运行）。
测试语义调整：修正输入后保留历史失败而 PARTIAL；损坏快照通过只读注入而非绕过 CAS 写保护；BuChE 无歧义续接及旧歧义拒绝分别覆盖；两项未来 Web/lab 接线测试留待 Task6。
旧快照缺少明确 revision 时拒绝恢复；新增计数/历史一致性、可变错误与容量边界测试。

父 compileall、8Node及contract通过。
独立规格审查本批288 passed，但7个独立探针失败，定位三个阻塞项：

- P1：首个完整观察封存晚于工具完成回调，回调可清除原 PROVIDER_ERROR，使内存科学回答与已落库错误不一致。
- P2：历史消息仅检查形状/计数，未逐条核对提案、观察与结果、后续用户输入；5个有效checksum但语义不一致的读注入达到CAS。
- P2：原始工具返回先进入adapter递归脱敏，再抵达harness预算门禁；65,537字符观察已在早拒绝前被扫描。

正独立修复，获准在当前分支最小扩展 session 的回调前封存及 adapter 的请求局部原始结果预算接口；不得改变默认静态执行与旧适配器保护。
质量审查、父联合回归、CI、具体 PR 授权尚未完成，不把 worker 通过作为总体完成。

## 本轮收口交接

修复 worker 已处理以上三项：完整观察在首次结果持久化/回调前封存；续接逐轮消息、提案、输入绑定、观察和复用关系在CAS前核对；请求局部raw-result门禁在legacy转换/normalize/脱敏前执行。
原7项独立失败已转绿并迁入正式测试；新增正式68 passed。worker完整Agent及4份顶层回归3096 passed、1 skipped、7 warnings，compileall/diff-check通过。
此为实现方验证；最终独立SPEC复审、质量审查、父联合回归、提交/PR/CI仍待执行，不宣称当前实现已获批准。
快照revision已从3升为4；旧revision3拒绝续接，需要新请求。
当前整体goal状态为paused，本轮仅收口已开展工作，不推进Task6、生产启用或其他PR合并。PR24已按用户授权squash合并为6aa5299；PR20对齐该main后的head b89a4dc最新CI run34696651404全7项通过，仍未获得具体合并授权。

## 再次申请集成（2026-09-12）

用户随后明确要求整理ADMET及本决策层、合并可合并项。本分支对齐main2a0fe75；PR #15/#20/#23已分别获授权合并。
父独立扩大回归3100 passed、1 skipped、7 warnings，103.47s，9Node、compileall、contract、diff-check通过。
最终SPEC复审确认原7探针全部转绿，正式356 passed、adapter/session兼容125 passed，但新对抗17 passed/2 failed，发现legacy字典同时success=True和error时转换会丢错误，再次出现假成功/下游消费。
正式2项RED后，在已校验容量的raw结果、legacy转换之前拒绝矛盾成功/错误，固定公开原因码，不泄露provider文本；70正式及19独立探针89 passed。最终SPEC复审继续，尚未签发批准，不进入合并。

相邻状态复核又独立发现14 failed/20 passed：failed/partial状态或字符串false可被legacy真值判断提升。正式典型8 RED后，增加success严格bool、success=True时error/status一致性门禁；78正式和53独立共131 passed。
最终SPEC明确PASS，原7探针、adapter/session默认兼容证据保持；转独立QUALITY，不将SPEC PASS代替质量/CI。
ADMET PR #25经用户具体授权合并为59e8cde后，本分支已普通merge该main；正在联合重验，完整harness仍未提交或启用。
本次父联合包含tests/agent及decision model、OpenAI-compatible、反幻觉、健康、ADMET fallback顶层测试：3161 passed、1 skipped、7 warnings，99.04s。compileall、contract及diff-check再次通过；质量复审仍进行中，不能凭联合绿灯提前发布。
QUALITY初轮进一步复现Typed ToolResult的success为字符串false或数字1也可通过；独立4 failed/2 passed，另8集成探针及14聚焦通过。正式4项RED后，在Typed原始观察入口严格检查bool，不使用强制转换。最终QUALITY复审和修订后父联合回归进行中。

## 最终本地门禁

最终QUALITY PASS：原4个typed假成功路径均明确失败，无下游调用、无数值输出；正常True/False及原8个独立集成探针共14 passed。无未解决本批P1/P2。
父最新联合3165 passed、1 skipped、7 warnings，101.67s；跳过为显式opt-in性能测试，warning为已有SWIG/FastAPI弃用。正式82项及原质量6探针88 passed；9Node、compileall、contract、diff-check通过。
暂存仅8个新harness模块、11个新测试、3个必要公共兼容文件及3份交接；凭据模式扫描无匹配。以main59e8cde为基线，不携带源分支无关代码或运行资产。
准备独立PR/CI与具体PR授权。工具catalog四只读项、revision4需新请求、未接生产Web入口、Task6与真实模型/部署验收未完成等边界不变。
