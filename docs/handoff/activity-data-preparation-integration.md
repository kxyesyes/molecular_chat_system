# 分批集成 3：活性数据准备

## 范围与来源

本批基于 main `38fc8c10eb48a7404ba0cb86d089419a6ea7e028`，使用独立分支
`codex/activity-data-preparation-integration`。原始开发工作树保持不变。

只移植历史检查点 `72371fb` 中的数据契约、离线 CSV/TSV 准备 CLI 与适用测试，
再纳入 `5432968` 中已审查的通用数据修复：源身份冲突拒绝、证据保留、
原始文本精度、NUL 预解析拒绝、E/Z 骨架往返一致性。
不整批合并历史分支，不复制历史训练或上线状态。

独立质量审查另发现历史实现允许 CSV/TSV 多列被推断为索引、缺列被自动补齐。
本批以 TDD 增加共享记录宽度检查，在 CLI 和源快照核对两处解析前执行；
这是相对于已审查历史实现的额外有界修复，不宣称生产文件仍与历史提交完全相同。

## 实施顺序

1. 在最新 main 的隔离工作树移植最小数据准备模块。
2. 在未修复基线运行回归测试，确认实际失败后移植已审查修复。
3. 运行数据准备、现有活性与 Agent 回归、编译检查；独立规格与质量审查。
4. 只提交本批文件，创建 draft PR；合并须另行确认具体 PR。

## 包含与不包含

- 包含 manifest 校验、分子标准化、测量关系/单位处理、重复测量证据、
  固定种子的骨架拆分、输入哈希、发布前往返校验与不覆盖已有目录。
- 原始和 prepared 数据默认忽略，仅允许其中 Markdown 说明跟踪；
  models 目录保持整体忽略。
- 不包含模型注册、训练、家族适配、家族推理、实验 Agent、界面或部署变更。
- 不读取或提交真实 PDE/BuChE 数据、API key、模型权重；不启用生产模型。
- 测试使用合成夹具，不能据此声称真实模型性能或科学预测成功。

## 验证记录

本地使用具备 RDKit/Pandas 的 MedChat Conda Python，pytest 参数统一为
`-B -m pytest ... -q -p no:cacheprovider --tb=short`。

- 回归红灯：三份 blockers/NUL/EZ 测试在未修复基线为 55 failed、1 passed，
  没有 collection/夹具缺失错误。基础移植测试另有 3 个 Git 忽略策略失败。
- 修复后 blockers/NUL/EZ：56 passed，无跳过；证据保存的完整断言已执行。
- 数据契约、CLI 与现有 prediction/registry 四文件：383 passed、2 skipped。
  两项为 Windows 符号链接权限/POSIX 竞态专属测试，不能记作通过。
- `tests/agent`：1670 passed、1 skipped（默认禁用的性能测试）。
- `python scripts/run_agent_acceptance.py --mode contract`：34/34 passed。
  这是离线契约验收，不代表真实科研工具或模型验收。
- `python -m compileall -q src scripts`：通过，缓存定向到系统临时目录。
- `tests/*_test.js`：8 个 Node 脚本均通过；未修改前端。
- root 分组（`tests --ignore=tests/agent --ignore=tests/sandbox_broker
  --ignore=tests/task_runtime`）：1511 passed、138 skipped、171 subtests passed。
  跳过包括 Windows/POSIX 边界、符号链接权限、未安装 Docker/promtool、
  未启用真实靶点查询及特权 Temporal/systemd 集成、被替代的旧部署用例。
- 独立规格审查通过，审查者补跑 77 项通过。
- 独立质量审查发现上述 P2 记录宽度问题；新增测试有效 RED 为 16 failed、2 passed。
  修复后的六份数据准备测试及现有 prediction/registry 测试共 457 passed、2 skipped
  （同上 Windows 平台原因），包含 18 个记录宽度/合法引用格式回归；编译复检通过。
  独立质量复审通过，审查者补跑记录宽度与 NUL 两组 44 passed，P2 已关闭。
  独立规格复审也通过，并补跑同两组 44 passed；确认仍为数据准备边界。
  修复后 root 分组重跑：1529 passed、138 skipped、171 subtests passed（291.52 秒），
  跳过原因与上轮相同；Agent 分组重跑仍为 1670 passed、1 skipped。

不同测试命令存在重叠，不将通过数累加宣称独立测试总数。
未运行部署 `health_check.py --strict`：本批是隔离离线数据准备代码移植，
未配置或启动模型、对接和数据库服务；不以缺少部署资产误判本批为已上线。

## 后续阶段

本批合并后，再单独审查并移植模型注册/准备数据训练，随后处理家族适配与推理。
实验 Agent 与真实外部服务验收仍独立处理，不并入数据准备 PR。

操作说明见 [活性数据准备 CLI](../activity_data_preparation.md)。
