# 分批集成 4：模型注册与训练接口

## 基线与边界

- 基线 main：`27027c0884980961e9228e7d55adc0d81723416d`（PR #9 已合并）。
- 独立分支：`codex/activity-model-training-integration`；原始混杂工作树不变。
- 移植 `4dfb26a` / `5dadb49` 的端点注册、模型卡、prepared 数据训练与适用测试。
- 保留 PR #9 的最新数据完整性检查，不从历史版本覆盖 `dataset_contract.py`。
- 只纳入 `36fc419` 的旧入口发现隔离修复，不带入 registry v3 家族 bundle 功能。
- 额外复用 PR #9 的严格记录校验，修复 prepared 加载器也会静默丢字段的问题。
- 校验保留的 row/replicate 靶点和终点身份，拒绝与 manifest 相矛盾的训练声明。
- CI 补齐匹配现有 Torch CPU ABI 的 scatter/sparse 扩展，不改本机环境。
- 不包含 Web 任意路径训练 API、家族推理、实验 Agent 或部署变更。
- 不读取真实训练数据或凭据，不启用生产模型；只用临时合成夹具验证工程行为。

## 实施计划

1. 从已合并 main 建隔离分支，核对历史文件与现有基线差异。
2. 先运行失败回归，移植通用端点注册与 prepared 训练接口。
3. 补齐发现隔离回归；校验注册不等于自动激活、训练不重新划分数据。
4. 聚焦回归、Agent/root 宽回归、编译与契约检查；独立规格、质量审查。
5. 精确暂存，独立 commit 与 draft PR；合并需另行明确确认。

## 设计约束

注册状态从 v1 原子迁移到 v2，保留旧全局选择，但端点映射初始为空。
`get_active_for_endpoint()` 不回退到其他端点；新 prepared 模型不会被旧全局入口
因列表优先级或历史路径自动拾取。显式选择接口继续兼容。

训练只读取已校验快照：train 更新权重、validation 选模、最佳权重只评估 test 一次。
特征失败不删行；prepared 输入不走旧 CSV 清理；注册失败只清理本次产物。
真实网络一轮烟雾测试仍使用合成标签，不是 PDE/BuChE 科研性能证据。

本阶段训练任务仍是进程内后台线程，不具备重启恢复；端点 ready 只表示契约就绪，
不代表科学性能达标或生产已激活。下一阶段再单独处理家族适配与推理。

## 验证与审查记录

- 基线有效 RED：训练元数据扩展与 prepared 入口回归 29 failed，无 collection 错误。
- 旧入口隔离有效 RED：17 failed、9 passed；测试不会加载合成占位权重。
- prepared 加载器额外回归有效 RED：8 failed，复现重算哈希后仍被误接受的
  CSV 多列隐式索引与 NUL 截断，以及校验晚于 DataFrame 解析的问题。
- 来源身份回归有效 RED：5 failed、8 passed；新增检查后与 prepared 数据测试一起
  得到 82 passed、3 skipped（Windows 符号链接权限）。
- CI 依赖声明回归有效 RED：2 failed；补齐后 2 passed。
- 旧入口隔离回归最终 26 passed。
- 独立规格审查通过；审查发现的 CPU CI 扩展缺失已修复。
- 独立质量审查发现实际中和后的特征输入可能跨拆分重叠，以及模型卡可选证据
  与注册记录矛盾。两项均需回归修复后复审，不以宽回归通过替代审查。
- 特征拆分问题有效 RED：3 failed；最小修复后相关训练/推理测试 40 passed。
  再补三种拆分配对、缺失身份及正常路径覆盖，特征完整性测试 10 passed。
- 质量审查者独立重跑该特征文件的 10 个用例通过，关闭 P1。
- 模型卡一致性有效 RED：矛盾声明未抛出 ValueError。修复后新增 86 passed，
  与 registry/endpoint 测试合跑 220 passed，现有 card 测试 17 passed（各计数有重叠）。
- 独立质量复审另执行 14 个模型卡聚焦用例通过，关闭 P2；当前完整工作树
  质量审查通过，无剩余阻塞项。提交前需重新精确暂存全部修复。
- 修复后最终宽回归：3534 passed、142 skipped、171 subtests passed，235.17s；
  9 条 warning 与前次相同类别，无失败。重新运行 compileall、contract 通过。
- 审查修复前本地宽回归：3438 passed、142 skipped、171 subtests passed，202.93s；
  覆盖 Agent 与 root，排除 sandbox/task runtime 两组。9 条 warning 为已有
  SWIG/FastAPI/PyG 弃用和 Tensor 构造性能提示，未静默屏蔽。
- contract：34/34 passed；8 个 Node `*_test.js` 脚本通过；compileall 和
  `git diff --check` 通过。文件名级敏感模式扫描无匹配，不输出任何凭据内容。
- Linux CI 结果在 PR 中记录；本地跳过不得记为通过。

以上测试在具备 RDKit/PyTorch/PyG 的 MedChat Conda 环境执行。关键命令：

```powershell
python -B -m pytest tests --ignore=tests/sandbox_broker --ignore=tests/task_runtime -q -p no:cacheprovider --tb=short
python -B -m pytest tests/test_activity_training_ci_dependencies.py -q -p no:cacheprovider
python -B scripts/run_agent_acceptance.py --mode contract
python -m compileall -q src scripts
Get-ChildItem tests -File -Filter '*_test.js' | Sort-Object Name | ForEach-Object { node $_.FullName }
git diff --check
```

compileall 实际通过临时 `pycache_prefix` 隔离缓存；不提交编译文件或验收输出。

## 未纳入的工作

- 本批不运行真实外部 API、Ollama 或真实 PDE/BuChE 数据训练，不读取凭据。
- 不执行依赖运行中全套服务的 `health_check.py --strict`；这是离线接口集成，
  不能据此声称生产环境或真实科研验收已通过。
- 不改动 sandbox/task runtime；这两组由既有远端 CI 分组验收。
- 本地 Windows 跳过的 POSIX/符号链接检查交由 Linux CI 验证；生产 systemd
  与显式 opt-in 的外部服务集成仍不等同于离线 CI。

使用说明：[端点注册](../activity_model_registry.md)、[prepared 训练](../activity_prepared_training.md)。
