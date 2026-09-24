# T10-A：RAG 工具类型契约

日期：2026-09-24。基线：main `3974f966e7adfc89f807af4b59da18ea2b60cf6c`。
用户已同意先完成 RAG 单工具契约及 TDD 实施；这不是整个 T10 完成声明。

## 批次范围

- 仅将注册表中的 `rag_search`（包含 `rag_database_search` 别名）从 Any 查询迁移到明确文本输入。
- 字符串与现有 `{query: text}` 在 Adapter 边界兼容，不改变原 query 文本；默认 k=3 不变，不新增 planner 参数。
- 输出契约覆盖原始工具结果与规范 ToolResult 的成功、partial、失败/拒绝/取消/不可用；不允许失败分支绕过记录结构校验。
- 只验证、不以 model_dump 覆盖原始数据。不改变既有 execute_tool_compat 脱敏/规范化规则，保留有效数据、任意 CSV 扩展字段、来源、warnings、evidence、artifacts、quality 和状态。
- 继续使用 ToolResult、ObservationStatus、AgentErrorCode；不创建第二套状态常量或执行器。其它工具的 schema、重试、超时、并发与生命周期保持不变。

## 真实边界

当前共享检索输出为记录列表。每条记录有 `source_index`、有限数值 `similarity_score`，以及 provenance 中 source_path/source_sha256/index_sha256/embedding_model/manifest_schema_version/builder_version/vector_label；CSV 其余列可扩展，不能要求某个并非固定存在的 SMILES 列。相似度不是概率，不限制为 0..1。

成功无命中列表合法。原工具失败 data=[]，规范化错误 data=None 都是现有合法形状；partial 可带经校验的部分记录，不能改成 succeeded。原始失败即使会在规范化时丢弃 data，也先检查其结构，避免借失败绕过契约。工具执行错误/超时/不可用仍返回现有错误码；契约错误使用 INVALID_INPUT 或 INVALID_OUTPUT，不泄露输入正文。

原始 dict 的状态别名/错误表示由既有核心解释；RAG 校验不得另写一套规范化。错误信封有字符串与结构化错误兼容形状，不擅自要求领域目前不提供的字段。合法 ToolResult 的 metadata 容器不因 Pydantic 投影而丢字段。

原始科学来源校验仍由 manifest/共享检索实现负责。新 schema 只证明结构合规，不能证明合成来源是真实科研来源；测试只能声称契约通过。

## 实现边界

建议新增 `src/agent/tooling/rag_contract.py` 承载 Pydantic 校验视图和窄 RAG Adapter；在 factory 仅为 RAG 选择该适配器与 schema。如果公共 ToolAdapter 需要让所有终态到达输出验证 hook，保持默认 hook 对非成功和无 schema 的原有无操作行为，并补非 RAG 回归。

原始验证放在执行线程内既有 raw-validator 边界，与上层 raw_validator 组合而非替换；工具只执行一次，沿用同一 slot、deadline 与规范化。校验失败不得附带未校验科学数据/格式化结果；诊断使用固定错误信息，保留合法失败信息和已验证内容，不把异常当成功。

不改变 RAGSystem 异步兼容接口 warning+[]；不读取生产索引/凭据、不调用真实 embedding、不升级依赖、不混入活性/对接/Planner 或 CLI 编码修复。

## 验收

实际 factory/registry/adapter 路径覆盖：字符串与 query dict、非文本拒绝且零调用、别名同对象、默认 k=3、成功/空结果、两种 partial 布尔形状、结构化失败/不可用、畸形记录/来源/非有限分数、矛盾状态、脱敏、扩展字段不丢失、raw_validator 保留及一次调用、并发/超时继续有效。原 manifest 真实模块矩阵与其它工具 Adapter 矩阵必须保持通过。

先 RED、后最小实现；保存实际失败与回归结果。独立规格审查再质量审查，精确提交/独立 PR；不直接写 main 或部署。遇到真实不兼容调用者时定位实际证据，不删除或弱化测试来伪造兼容。
