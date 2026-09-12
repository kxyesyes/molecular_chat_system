# ADMET 完整分子输入集成

日期：2026-09-12。分支 `codex/admet-whole-input-integration`，当前基线 main `2a0fe75`。
仅修复历史混杂树完整 SMILES 解析残差中的 ADMET 实际调用入口；原始工作区不修改。

## 实际复现与范围

在干净集成树调用 ADMET.execute，强制使用模块内已标识的真实 RDKit rules fallback，不调用外部模型：

- `CCO\nOCC\nCCN` 仅得到 CCO，并报告 success。
- `SMILES: CCO invalid_suffix` 被截取成 CCO 并报告 success。

这是实际工具输入问题，不再以 PropertyCalculator 继承的 extractor 探针推断其 execute 有问题。
原因是 ADMET.should_use/execute 仍使用 legacy BaseMolecularTool.extract_smiles，而性质/类药性入口已采用更严格的完整结构解析器。

复用现有 `parse_molecular_smiles`，不改 BaseMolecularTool 或生产模型；独立审查发现 ADMET 正文术语歧义后，共享 parser 增加默认关闭的局部正文短语钩子，其他调用者默认行为保持不变。
多行合法输入逐项保留原始顺序和 SMILES 表示，任何无效字段在调用 ADME 后端前整体拒绝；禁止小写 cco 修复成 CCO。
需要真实 RDKit 完整结构校验；缺失时明确失败，不恢复词法启发式兜底。

## 验证

正式新测试13项加已有fallback测试1项：**11 failed、3 passed → 14 passed**。
覆盖后端实际收到全部输入、非法后缀、有效/无效混合批次、CXSMILES后缀、大小写、触发一致性和缺失 RDKit。
随后解析器 RuntimeError 透传回归先1 failed，补暂不可用的固定失败信息后共15 passed；不把服务异常判断成结构无效，不把内部诊断透传。
真实规则结果仍记录 prediction_method=rdkit_rules，不能称为训练模型预测或实验结论。
扩大回归第一轮 `pytest tests/agent tests/test_admet_predictor_fallback.py tests/test_agent_anti_hallucination_fallbacks.py tests/test_agent_platform_health_check.py -q -p no:cacheprovider --tb=short`：2686 passed、1 skipped、7 warnings，59.71s。
最后异常分类修订后的同组扩大回归：2687 passed、1 skipped、7 warnings，57.69s。

独立规格首轮发现 BBB/CNS 正文缩写被解析为额外分子（基线实际execute仅CCO，新实现多BBB/CNS），正式4项RED后修复。
初版全局忽略术语又可丢掉以BBB开头的坏字段；追加边界4项RED，改成局部完整短语，并保留显式/独立BBB/CNS分子。
规格再次发现合法短语前缀吞掉同token非法后缀；正式3项RED，限制完整token覆盖或单个明确句末标点。
最后精确单标点的字符串成员判断又被规格探针发现会接受多标点子串；正式6 RED，改两处为精确元组后277项共享聚焦通过。
独立最终 SPEC 批准：232 passed，其中100个独立探针；无剩余输入保真阻塞项。质量审查进行中。
正文消歧版扩大回归2699 passed、1 skipped、7 warnings，57.27s；最后精确标点修订后全回归/CI待完成。
精确标点修订后扩大回归2708 passed、1 skipped、7 warnings，55.65s。
质量初轮286项通过，但独立13 failed/11 passed，定位间隔短语未匹配后缀仍被启发式忽略，以及底层 RDKit ValueError 被作为公开校验消息透传。
正式7项RED后：所有短语重叠但不满足完整豁免的token直接拒绝；RDKit外部调用异常转固定消息的 MolecularInputUnavailable（ValueError兼容子类），不诊断原始结构无效、不泄露底层文本。
共享聚焦284 passed；最后扩大回归2715 passed、1 skipped、7 warnings，56.01s；compileall、8Node、contract及diff-check通过。
质量复审发现中文短语末尾不与ASCII尾部同token，16项独立边界失败；正式8项RED复现后，在原始短语右边界检查紧邻尾部，不依赖中文分词是否重叠。
该修订正式及独立复审探针132 passed；最终扩大回归2723 passed、1 skipped、7 warnings，59.74s。
最后独立 QUALITY 批准：407 passed（正式聚焦和前两轮106项独立探针），额外96项正向中文组合通过；86输入×2默认调用方对照仍与基线一致，32项外部异常注入通过，无剩余本批审查问题。
对齐包含 PR #15/#20/#23 的 main 2a0fe75 后，两份源码仍与最终QUALITY批准的blob 97279ee/768840d相同；扩大回归2723 passed、1 skipped、7 warnings，56.66s。
最终9个Node脚本、compileall、contract及diff-check通过。准备独立PR，等待最新CI及具体PR合并授权；不启用模型或重启服务。

## 未包括事项

- reverse_target 的单分子接口和 RXN 等 legacy extractor 调用者仍需分别验证，不做全局替换。
- ADME 后端某行计算失败而其余行成功的现有批次结果语义尚未在本批修复；本批证明所有有效输入进入后端，不等于所有模型计算成功。
- 不读取真实 CSV、模型权重、密钥；不启动或重启生产服务。部署验收未完成。
