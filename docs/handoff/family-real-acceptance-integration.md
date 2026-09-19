# 家族真实权重隔离验收集成交接

## 范围和当前状态（2026-09-19）

用户已选择子代理逐任务实施，采用TDD及独立SPEC→QUALITY审查。
分支：`codex/family-real-acceptance-integration`；实施起点`415b24d`，main基线`57680aa`。
依据[已批准设计](../superpowers/specs/2026-09-15-family-real-acceptance-design.md)及
[七任务实施计划](../superpowers/plans/2026-09-15-family-real-acceptance-integration.md)。

本批只做代码与合成资产工程验收。不读取真实模型目录/权重、原始数据集、.env或API凭据，不调用外部模型，不启用生产模型。
真实权重执行、生产接管、历史报告展示残差和部署验证仍是后续事项。
原始混杂工作树的13项已修改/未跟踪路径与任务开始前一致，没有覆盖或暂存。

## 实施与审查进度

| 任务 | 当前状态 |
|---|---|
| 1. 配置与合成前向fixture | 完成，SPEC/QUALITY均通过，最终135 passed |
| 2. 精确bundle快照与摘要 | 下一实施项 |
| 3. 隔离进程与资源回收 | 未实施 |
| 4. DOM fixture与生产渲染断言 | 未实施 |
| 5. 预测/API/逐轮决策/ASGI链路 | 未实施 |
| 6. opt-in入口与报告 | 未实施 |
| 7. 全回归、双审、PR | 未完成 |

## 已取得的验证证据

- 实施前现有10个根目录Node测试脚本全部通过，其中家族DOM18项、隔离lab30项；不是浏览器或真实模型测试。
- 实施前Agent/model/fallback联合基线：3288 passed、2 skipped、7 warnings，123.08秒；没有把两个skip计为科研通过。运行命令为计划Task7 Step2的Python联合回归命令，使用指定MedChat解释器并关闭真实验收开关。
- CI实际配置是`.github/workflows/quality.yml`；root pytest组自动收集根目录新增Python测试，static组只运行`*_test.js`。新的DOM验收驱动需要由其Python链路/Node既有测试显式覆盖，不能只创建文件就声称CI覆盖。
- Python科学环境固定为MedChat Conda解释器；不使用缺RDKit/LangGraph的默认环境。

本记录会随每项真实执行与审查结果更新，未标完成的项不可解释为通过。

## Task1证据

- `8835cee`：默认关闭的纯配置解析、精确bundle配置、合成真实RGNN前向fixture；旧推理断言保留。实施者RED61 failed（待建接口不存在），GREEN130 passed/2 warnings。首次确定性测试对torch.save归档字节做不当相等断言，已改为比较真实张量，不把文件名导致的归档差异误称模型差异。
- 独立SPEC运行130 passed/2 warnings，发现CPU fixture使用torch.manual_seed会同时设置加速器种子，与仅恢复CPU RNG的边界不符。父级核对本机PyTorch该函数源码，确认反馈成立。
- `0fd493b`：改用CPU default_generator.manual_seed(71)。实施者记录RED2 failed/1 passed（成功/异常均捕获4次加速器播种），聚焦GREEN3 passed、Task1回归133 passed/2 warnings；种子对应的真实权重张量和前向值与独立CPU参考完全相同。等待独立复审，不能提前称双审完成。
- 父级contract命令通过；RDKit对固定无效SMILES的解析错误是预期拒绝日志，不是科研计算成功。
- 独立SPEC复审通过（6 passed/58 deselected）；QUALITY独立全聚焦133 passed仍发现默认设备问题：后置`.to(cpu)`不能约束此前的特征/网络构造。其无GPU的meta-device探针复现失败、局部CPU context对照成功。已要求补RED/GREEN，修复前不进入下一任务。
- `e4a8f24`：局部CPU context覆盖完整构造；实施者RED2 failed→GREEN5 passed→全聚焦135 passed/2 warnings。独立SPEC增量复审meta2项+seed71一项通过；独立QUALITY重新运行完整三个文件135 passed/2 warnings并批准，无剩余审查项。本批使用子代理TDD和双审发现并关闭了上述两处隔离缺陷，未改生产代码。
