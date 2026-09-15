# 隔离服务端聊天桥接集成

日期：2026-09-15。分支：`codex/agent-isolated-chat-integration`。
基线：main `4cef43b`，GitHub compare API 确认 identical，随后git ls-remote亦确认同一提交。

## 范围

用户确认先只移植服务端聊天桥接。新增 `src/web/decision_chat.py`、ChatHandler显式委托方法和对应测试；生产WebSocket分派、首页、模型配置不变。源 `9312bf5` 的无界队列改为至多128个待发送事件，跨线程通知合并，异常/过载显式failed。普通事件原始内容上限64KiB；终态聚合先按4MiB/65536节点校验，再压缩成状态摘要，摘要同样经过64KiB边界。完整结果从agent_result独立发送，省略显示明确标记。每次WebSocket发送期限30秒。

Harness继续负责工具授权、真实科学结果、证据、续接和取消状态。桥接不重写科学文本、不回放工具。取消时等待受控harness任务结算；这不保证不合作的外部计算已被强制终止。传输失败与harness已有运行状态是不同层次，不覆盖持久化科学证据。

## TDD证据

- 历史桥接19项先RED：全部缺少`process_decision_message`，无collection错误。
- 新传输用例11 failed/1 passed：11项缺入口；1项验证生产入口没有调用新桥接。
- 实现后31 passed。
- 补充“溢出清理期间调用方取消”用例先RED：取消被吞掉；改用shield/gather区分子任务取消与调用方取消，清理后重新抛出。
- 新增真实FastAPI WebSocket+RDKit验证、事件回调后原对象修改不污染已发送快照；纠正测试对分子量舍入和持久化状态的错误预期，不改生产数值或状态协议，聚焦34 passed。
- QUALITY审查发现12分子的真实RDKit批次完成事件超过64KiB，计算成功被误报为传输失败。8/12/20分子三项回归先RED，再将终态重复结果改为有界状态摘要；完整分子行仍从agent_result传送，修复后聚焦37 passed。
- 首轮联合3196 passed/1 skipped、随后3199 passed/1 skipped；均发生在最终批量事件修复前，保留为阶段性证据。最终联合 **3202 passed/1 skipped/7 warnings，105.50秒**。

## 已运行命令

使用具备科学依赖的MedChat Conda环境（不读取密钥），命令路径相对于本工作树：

```powershell
python -B -m pytest tests/agent/test_decision_chat.py tests/agent/test_decision_chat_transport.py -q -p no:cacheprovider --tb=short
python -B -m pytest tests/agent tests/test_agent_decision_model.py tests/test_openai_compatible_model.py tests/test_agent_anti_hallucination_fallbacks.py tests/test_agent_platform_health_check.py tests/test_admet_predictor_fallback.py -q -p no:cacheprovider --tb=short
python -m compileall -q src scripts
python -B scripts/run_agent_acceptance.py --mode contract
```

已完成：聚焦37 passed、9个Node脚本通过、compileall通过、contract报告passed。无效SMILES解析警告是拒绝用例的预期诊断。联合回归唯一skip为未显式启用的LangGraph shadow性能测试；SWIG/FastAPI弃用警告不隐瞒。

SPEC独立审查通过，另以实际harness验证错误session、revision3快照、校验和有效但历史语义矛盾均在CAS/模型/工具执行前拒绝。父进程复验历史桥接19项和三项审查探针共22 passed。QUALITY首审P2批量问题已修复，最终复审PASS：37项正式测试及12项独立有界资源/终态探针共49 passed，无新增意见。PR/远程CI尚待发布验证，不将本地通过等同合并完成。

## 未完成/不在本批

- `decision_lab.py`、静态lab和CLI验收入口尚待下一批集成。
- 本批模型调用使用显式测试替身；RDKit用例运行实际计算，不将其说成真实外部模型验收。
- 真实家族权重全链路测试、原始混杂树报告展示残差、目标服务器测试继续保留。
- 未修改原始工作树；没有训练、启用模型、重启服务、读取真实数据/凭据或直接修改main。
