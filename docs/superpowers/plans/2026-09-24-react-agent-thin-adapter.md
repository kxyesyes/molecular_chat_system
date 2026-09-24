# ReAct Thin Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 保留旧 ReAct 公共接口，科学执行与状态统一到 Supervisor/Session，不丢证据、不旁路校验。

**Architecture:** 工具工厂和模型隔离保持，ReAct 只负责入参转交与兼容结果投影。状态从 AgentResult 取得，事件从规范执行取得；旧推理/回退循环在科学断言迁移后删除。共享路由风险先实际复现，必要扩大范围先报告。

**Tech Stack:** Python、pytest、现有 Supervisor/WorkflowRunSession/ToolResult；无新增依赖。

用户已确认书面设计，工作树 `D:/MedChat/molecular_chat_system_worktrees/react-agent-thin-adapter`。
基线 `50bf8a7`，书面设计提交 `e3db6c9` / `9e1ce18`；PR #55 依赖尚未合并。

## 执行检查点（2026-09-24）

- Task1/2/3/4：已实现公共入口薄适配、迁移六份科学测试并删除旧私有循环。
  Task3 独立规格/质量审查通过；失败、状态、计数、模型隔离和资源边界保留。
- 用户另明确批准两处共享修复：自动路由确认门禁及大写pIC50来源校验。
  首次联合回归5376passed/9failed；扩大负向测试后42passed/14failed；最小修复
  后九文件318passed。完整失败记录和明确适用边界见交接文档，不削弱科学断言。
- Task5：最终23路径5393passed、9skipped；contract34/34、306文件内存编译通过。
  整批独立规格/质量审查通过，审查发现的路由统计退化已按TDD修复并复审。
  本地精确提交，不推送/合并、不改变生产入口；这不代表整个任务书完成。

## Task 1：隔离复现与规范入口检查

文件：新增 `tests/agent/test_react_agent_adapter.py`；检查 supervisor.py、router.py。

- [x] 从已记录六项诊断建立公开构造/execute 测试，只替换工具工厂及合成模型。
  断言完整失败、warning/error、demo 禁止；不要断言旧错误路径才有的 action_input 改写。
- [x] 新增真实 Router→Supervisor 与 ReAct 的无效 SMILES / 缺 docking 输入拒绝测试：

```python
decision = agent.skill_router.decide(query)
assert decision.requires_confirmation
result = agent.execute(query)
assert tool.calls == []
assert result["success"] is False
```

- [x] 运行新文件，确认 RED 是行为违反而非夹具错误；记录各分组结果。
- [x] 若共享入口仍调用工具，报告最小共享拒绝修复，不往 ReAct 复制新的安全规则。

## Task 2：公开返回与参数契约

文件：新增 `tests/agent/test_react_agent_adapter.py`；修改 `src/agent/react_agent.py`。

- [x] 建立结果投影测试：成功/partial/failed/rejected/cancelled，重复观察、错误/来源/产物，
  真实事件调用顺序、无工具绑定错误、无匹配，旧格式化与方法签名。
- [x] 每项先确认失败，再使用规范序列化实现：

```python
canonical = raw["agent_result"].to_legacy_dict()
result = {**raw, **canonical, "query": query, "steps": [],
          "reasoning_trace": [],
          "tools_used": [e["tool"] for e in raw["agent_events"]
                         if e.get("event") == "tool_started"]}
```

  无 agent_result 分支保留真实非成功原因，补兼容空字段，不捏造观察或执行事件。
- [x] 构造保持 get_all_tools(molecular_generator_llm)，规范别名使用 TOOL_ALIASES。
  每次执行只创建轻量 Supervisor，使用同一工具实例/路由器与当前模型；不创建新存储。
- [x] 省略 mol_count 时不传递；显式值原样传递，使用现有校验；temperature 与
  event_callback 原样传递。禁止 current_* 请求参数写到 self。
- [x] set_llm 复用既有主模型消费者行为，专用生成器及别名不重绑；不关闭借入资源。

## Task 3：迁移既有科学断言

文件：`tests/test_agent_llm_wiring.py`；`tests/agent/test_react_agent_workflow_routing.py`、
`test_workflow_skills.py`、`test_comprehensive_workflow.py`、`test_family_activity_tool.py`、
`test_target_driven_design_workflow.py`。

- [x] 替换 __new__/私有执行方法夹具为真实构造加受控工具工厂；保留共享 FakeTool 的既有用途。
- [x] 禁止工具通过公开执行验证零调用；允许 RAG 别名需返回合法来源结构或明确无命中，
  不能只给 formatted 字符串伪装真实成功。
- [x] 生成夹具返回请求数量的有效唯一候选和来源，再断言共享参数：

```python
assert generator.calls[0]["metadata"]["requested_count"] == count
assert generator.calls[0]["metadata"]["temperature"] == temperature
assert forbidden.calls == []
```

- [x] 保留引用/否定、显式数量优先、超限错误、原始靶点传递、专用模型断言。
  无工具普通解释可变为明确不匹配，不保留旧空结果 success=true。
- [x] partial 总体判断严格跟 AgentResult；保留观察 partial/success=false 的内容，
  不私自升级总体状态。每个改动在交接说明原断言→新公共入口覆盖。

## Task 4：删除重复循环并验证一次执行

文件：`src/agent/react_agent.py`、新旧相关测试。

- [x] 在覆盖完成后删除私有 action 文本解析、关键词回退、直接工具执行、独立状态汇总。
  保留 ReActStep/format_result，核对 prompts 常量其他消费者，不删共享模块。
- [x] 新测试禁止适配器中 tool.execute / 重复执行；真实生成器验证温度/数量进入模型参数。
- [x] 实测异常、取消、超时沿用规范执行；测试前后借入工具 close 计数不增加。

## Task 5：联合验证、独立审查与交接

文件：更新 `docs/AGENT_MAINTENANCE.md`；新增 `docs/handoff/react-agent-thin-adapter.md`。

- [x] 聚焦新文件及六份迁移测试，随后 Agent/Web 23 路径联合回归。
- [x] 离线 contract 34 案例（读取报告状态）、src/scripts 内存 compile、git diff --check。
- [x] 独立 SPEC/QUALITY 审查；对重要问题按实际 RED→GREEN 修复，不删除安全断言。
- [x] 记录真实失败和跳过原因、接口语义变化、未完成项、未部署/未启用模型。
- [x] 精确暂存本批范围，本地提交；依赖 PR55 未合并时不将父批变成新的大 PR。

## 隔离运行命令

复用已经验证的临时配置/数据库 runner，不读取生产 key、索引、任务库或真实权重。

```powershell
$python='C:/Users/xkx52/.conda/envs/MedChat/python.exe'
$plan=Get-Content docs/superpowers/plans/2026-09-24-rag-service-extraction.md -Raw
$m=[regex]::Match($plan,'(?s)\$runner = @''\r?\n(.*?)\r?\n''@')
if(-not $m.Success){throw 'Isolation runner missing'}
$runner=$m.Groups[1].Value.Replace('D:/MedChat/molecular_chat_system_worktrees/rag-service-extraction-pr','D:/MedChat/molecular_chat_system_worktrees/react-agent-thin-adapter')
$focus=@('tests/agent/test_react_agent_adapter.py','tests/test_agent_llm_wiring.py','tests/agent/test_react_agent_workflow_routing.py','tests/agent/test_workflow_skills.py','tests/agent/test_comprehensive_workflow.py','tests/agent/test_family_activity_tool.py','tests/agent/test_target_driven_design_workflow.py')
$runner | & $python -B -c "import sys; exec(sys.stdin.read())" @focus
```

内部 pytest 使用 `-q -p no:cacheprovider --tb=short -rs`，失败退出码不可忽略。
联合路径沿用 `docs/handoff/molecular-agent-thin-adapter.md` 中完整23项列表，首项 tests/agent
已包含新增测试。contract 使用同一临时环境，runpy 运行验收脚本并阻断 socket，报告输出
ignored scratch/react-agent-contract.json。不得用 real/all 代替本批离线回归。
