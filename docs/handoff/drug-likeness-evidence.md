# 类药性证据边界与评分参数修复交接

## 任务与边界

用户已确认B方案和书面设计，分支 `codex/drug-likeness-evidence-fix`，基线main `4ff483d`、设计提交 `fe947c8`。
使用实施计划、TDD和独立规格/质量审查。只修正既有类药性工具的评分参数传递与解释文字；
不改变RDKit计算、权重、公式、规则阈值、公开结果结构、错误状态、历史run/checkpoint或模型配置。
原始工作树保持不动。仅本地实现/测试/提交，不推送、创建PR、合并、部署或调用真实模型。

## 已复现根因

`assess_drug_likeness` 将Lipinski结果字典长度4作为违反数量，且把Veber、lead两个非空结果字典作为布尔值。
因此Lipinski分项固定0、另两分项固定1，虽然结构化规则结果可能恰好相反。
单分子解释还把零违反项变成未经模型/实验支持的口服生物利用度肯定结论。

## 基线与期望变化

父任务从Git读取不可变 `fe947c8` 源码，在隔离临时配置中执行实际RDKit工具，
然后独立按原公式及正确标量参数计算期望总分。此表不是旧记录迁移，也不是实验活性或生物利用度预测：

| 测试分子 | 基线总分 | 原公式正确输入对应总分 | 违反项 / Veber / lead |
|---|---:|---:|---|
| CCO | 0.463 | 0.693 | 0 / true / false |
| 20碳直链 | 0.395 | 0.450 | 1 / false / false |
| 40碳直链 | 0.326 | 0.306 | 2 / false / false |
| 普萘洛尔测试SMILES | 0.635 | 0.935 | 0 / true / true |

QED使用未舍入RDKit值计算综合分数；展示QED及最终总分仍按原精度舍入。
综合分数和评级随纠错变化，不能宣称与错误旧结果完全一致或重新标定了模型。

## 文件职责

- `src/agent/tools/drug_likeness_assessment.py`：唯一计划修改的生产文件。
- `tests/agent/test_drug_likeness_evidence.py`：真实RDKit、公式边界、单批量文案、失败和适配契约回归。
- 同日spec/plan、本交接、latest：批准范围、过程及验证证据。

## 验证记录

实施者使用既有隔离runner的实际记录：

1. 首次RED：**28 failed、96 passed、1 warning，4.15s，exit 1**。
2. 将测试参数迭代器显式转为list消除测试自身警告，并独立参数化批量reasoning断言；最终RED：**29 failed、96 passed，2.31s，exit 1**。
3. 最小修复后同一新增文件：**125 passed，1.79s，exit 0**，无skip/warnings。
4. 下列五文件聚焦：**313 passed，12.13s，exit 0**，无skip/warnings。

修复仅为三个参数取字段及证据限定文案；实施者对照基线确认评分函数、三个规则函数源码完全不变，
除评分调用外的描述符计算、结果结构与计算异常处理AST一致。
父任务再次独立AST核对：只有execute、assess_drug_likeness、format_assessment、interpretation、brief reasoning变化，
评分函数和规则函数保持不变；临时字节码目录中的src/scripts/新增测试compileall通过，目录已清理，diff通过。

独立SPEC **APPROVED**，无遗漏或越界；独立五文件聚焦 **313 passed，10.86s，exit 0**，无skip/warnings。
该审查没有重新执行历史RED，不将实施者过程记录当作独立复现。
独立QUALITY **APPROVED**，无发现；独立新测试 **125 passed，1.86s，exit 0**，无skip/warnings，
前后源码/测试哈希一致，diff通过。两个审查代理与实施代理均已关闭。

父任务对修复后实际execute再作隔离探针：四个分子总分分别为0.693、0.450、0.306、0.935，
与表中独立期望一致；全部不含原过度结论，全部包含不能确定生物利用度/疗效的说明。
最终冻结2个生产/测试路径，运行前后SHA-256一致；全部Agent及下列六个Web模块联合回归：
**5741 passed、7 skipped、7 warnings，405.22s，exit 0**。不是全仓或真实模型验收。

7个skip：目录符号链接不可用1项、显式关闭性能测试1项、POSIX目录权限2项、需要两独立任务store2项、需要配置runtime1项。
7个warnings：既有SWIG弃用3项、FastAPI `on_event` 弃用4项。未修改skip/warnings策略、CI或超时。
未改JS/模板，因此未单独运行Node；联合中的静态页面资产检查已执行。未改部署或运行资产，未运行真实服务健康检查。
编译、diff与任务文件常见凭据模式扫描通过，候选0（不等于完整安全审计）。

本批精确暂存6路径、本地提交；提交号见本分支git log和本轮回复，避免文档自引用。
全部测试和代理已收齐，原始13项混杂改动未操作。尚未推送、创建PR、合并或部署。

## 可复核方式

Python使用MedChat Conda，runner从 `docs/superpowers/plans/2026-09-24-rag-service-extraction.md` 提取，
只将repo替换为当前独立工作树。实际pytest在临时工作目录，业务环境变量清除，
配置/Agent/Task/session/target数据隔离；真实模型及canary关闭，不输出或读取真实Key。

```powershell
$runner | & $python -B -c "import sys; exec(sys.stdin.read())" tests/agent/test_drug_likeness_evidence.py
$runner | & $python -B -c "import sys; exec(sys.stdin.read())" tests/agent/test_drug_likeness_evidence.py tests/agent/test_explicit_molecular_input.py tests/agent/test_property_report_boundaries.py tests/agent/test_decision_inputs.py tests/agent/test_decision_chat.py
$runner | & $python -B -c "import sys; exec(sys.stdin.read())" tests/agent tests/test_agent_session.py tests/test_agent_session_entrypoints.py tests/test_agent_task_ownership.py tests/test_phase2_phase3_routes.py tests/test_web_app_lifecycle.py tests/test_static_placeholder_cleanup.py
git diff --check
```

## 保留限制

- 历史保存的旧评分/旧文案不被改写；读取旧checkpoint仍可能显示旧结果，只有重新计算应用修正。
- 规则评分不是成药成功概率；未建立或验收新的生物利用度、疗效或药代模型。
- 不将局部修复称为全仓无bug、全部科学结论可靠或生产已经更新。
- 既有ToolResult适配不保留独立reasoning字段；本批未扩展该公共契约，已验证formatted中的证据边界、真实数值和上游元数据保留。
