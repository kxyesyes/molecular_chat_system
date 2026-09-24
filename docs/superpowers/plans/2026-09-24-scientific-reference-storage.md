# T09 第一增量：科研引用契约与原子存储实施计划

> **For agentic workers:** Use subagent-driven-development for the independent contract task and review; coordinator implements the coupled persistence boundary using TDD. Steps below track this increment, not all T09.

**Goal:** 在现有 SQLite metadata 上建立有界、会话归属绑定、展示确认后可恢复的科研候选快照，不接入生产入口。

**Architecture:** `ScientificPresentation` 复用 CandidateRecord，序列化内容生成不可变 revision。SQLite 专用方法从已持久化成功候选 checkpoint 投影，不接受浏览器提供科学内容；事务内核对归属和来源版本。普通 metadata 写入不得伪造该命名空间。

**Tech Stack:** Python dataclass、标准库 JSON/SHA256、现有 SQLite/CandidateSet、pytest；不增加依赖或表。

## 1. 契约（独立任务）

Create `src/agent/contracts/scientific_references.py` and `tests/agent/test_scientific_reference_contracts.py`.

- [x] RED：建立真实契约测试，先通过 `hasattr(module, 'ScientificPresentation')` 断言缺少实现。
- [x] 实现 `ScientificPresentation.create(*, source_trace_id, source_version, source_status, ordered_candidates, target, evidence, warnings, created_at)`、`from_dict(value)`、`to_dict()`、`ordered_keys`。
- [x] `ordered_candidates` 每项严格为 `{observation_id, candidate}`；candidate 使用完整 CandidateRecord 序列化，不重建科学模型。列表非空、至多32项，复合键唯一、canonical_smiles 唯一。`source_status` 仅 succeeded/partial；target 为明确字符串或 null；evidence 为对象数组；warnings 为字符串数组。
- [x] create 生成 UUID presentation_id，固定 expires_at=created_at+86400；revision 为除自身外完整规范 JSON 的 SHA256。source_version 为64位小写十六进制来源摘要。schema_version 严格整数1；未知字段、篡改摘要、非法/非有限时间拒绝。
- [x] 存储为不可变值；to_dict 深度脱离。`ordered_keys` 返回 `[(observation_id, candidate_id), ...]`。契约不声称自己运行 RDKit；创建方必须已验证来源。
- [x] JSON 在复制、凭据检查与摘要前受512KiB、32层、65536节点限制；凭据或 redaction 改变内容时拒绝。公开 `reference_json(value)` 返回严格 JSON 编码供 namespace 总预算复用，不引入全局序列化重构。
- [x] GREEN：测试创建/回读、不可变性、顺序、身份、大小/深度/循环/凭据、时间、partial 状态。契约与存储为同一增量，待联合审查结束统一精确提交，未让子代理单独提交。

## 2. 存储（协调者关键路径）

Modify `src/agent/persistence/base.py`, `sqlite_store.py`; create `src/agent/persistence/scientific_references.py` and `tests/agent/test_scientific_reference_store.py`.

Public signatures:

```python
publish_scientific_presentation(trace_id, *, session_id, selections, target=None)
# selections: ordered list of {observation_id: checkpoint_id, candidate_id: str}.
# Returns presentation dict or None. Scientific content is read from checkpoints.
confirm_scientific_presentation(trace_id, *, session_id, presentation_id,
                                revision, ordered_keys) -> bool
get_scientific_presentation(trace_id, *, session_id, presentation_id,
                            revision) -> dict | None
# Only confirmed, still-valid views are returned.
```

- [x] RED：实际临时SQLite验证发布后不可读、精确顺序ACK后可读、新store重启可读；错会话/trace/revision均拒绝。
- [x] 私有 helper 使用既有连接和锁；所有专用操作事务内查询 owner、run status/workflow_version 和 checkpoint 集合摘要。只接受同trace最新step checkpoint 的 succeeded/partial、success=true、CandidateSet@1 和合法 CandidateSet；来源工具需有 provenance，demo/fallback 不准引用。保留 warnings/evidence，不吞掉错误。
- [x] source_version 摘要覆盖run身份、workflow_version、状态与checkpoint全部持久字段；任何新增/变更来源均使旧引用失效。不把updated_at作为版本，以免普通metadata更新造成误失效。
- [x] `scientific_presentations` namespace 保存最多8个 `{presentation, confirmed}`。发布时间来自服务端time.time；固定24小时；专用发布清理过期引用但不删checkpoint，重复发布相同来源/顺序/target返回原快照、不续期。
- [x] namespace超512KiB/损坏拒绝；缺归属、来源错误拒绝；SQLite异常透传且提交后才返回成功。专用连接显式关闭。
- [x] 普通start/claim/update不能注入namespace；start不能替换已有引用run，claim重新执行使旧引用失效；普通metadata更新保留namespace，decision continuation状态变化使引用失效且不破坏原CAS语义。
- [x] GREEN：增加固定时钟边界、来源变更、无效/失败工具、伪造字段、容量、两store并发、写入/提交故障、普通写入保护回归。

## 3. 验证与交接

- [x] 聚焦命令：使用已有隔离runner（本仓 `2026-09-24-rag-service-extraction.md`），仅替换repo为本worktree，运行新增两模块及 candidate contracts、decision continuation store、persistence 和 ownership 相关测试。
- [x] 隔离运行 `tests/agent` 与相关Web ownership/session测试；不读取真实模型配置，不运行real验收。最终冻结源码5527 passed、7 skipped、7 warnings，285.36s。
- [x] 源码编译、git diff --check、独立规格审查，再做独立质量审查；所有问题已修复。独立最终质量复审运行新增152项全部通过；下一操作仅显式暂存本批文件并本地提交。
- [x] 更新本计划和handoff，记录实际RED/GREEN及未完成项；不宣称Web ACK/两轮工具输入已接线。

实施记录和全部失败尝试见 `docs/handoff/scientific-reference-storage.md`。首次探索整合与最终冻结运行分开记录；最终6个生产/测试文件SHA256前后一致。第一增量不代表全部T09，后续Web与全链路任务未删除。

## 后续增量与已知边界

下一增量接线Web投影、实际显示ACK、标签页恢复和选中对象到工具输入；再下一增量验证多轮/重启/取消/工具禁用与前端兼容。当前专用API仅供受信服务端调用，不直接公开checkpoint查询，也不能单凭契约中的RDKit标记证明化学有效。现有科学Validator仍是科学证据来源。具体PR合并另行核对授权和CI。
