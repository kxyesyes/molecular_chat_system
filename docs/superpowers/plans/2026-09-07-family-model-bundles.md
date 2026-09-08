# Family Model Bundles Implementation Plan

> **For agentic workers:** Use subagent-driven-development and test-driven-development; perform spec review then quality review before committing.

**Goal:** 复用注册表与 RGNN，交付家族双模型成组管理及隔离的两阶段 Python 推理入口。

**Architecture:** 扩展现有注册表事务状态，持久化家族包和活动包映射；注册时绑定已验证
family 数据描述及两个 endpoint-ready 模型。新增独立 family predictor，固定读取一组
经校验的模型快照，不调用 legacy/global 模型选择。Web/Agent 接入和真实训练留给下一阶段。

**Tech Stack:** Python、现有 JSON 注册事务/文件锁、PyTorch/PyG/RDKit、pytest。

## Task 1：模型组事务与数据绑定

Files: `src/activity/model_registry.py`、新增 `src/activity/family_models.py`、
`tests/test_activity_family_models.py`，必要时新增独立合成测试支持文件。

- [x] TDD：先写缺失 API 的失败测试，再实现以下接口，参数固定且禁止隐式激活。

```python
record = registry.register_family_bundle(
    bundle_id="pde-v1", family_dataset_path=family_manifest,
    classification_model_id="classifier", regression_model_id="regressor")
assert registry.get_active_family_bundle("PDE5A") is None
registry.select_family_bundle("pde-v1")
assert registry.get_active_family_bundle("PDE5A")["bundle_id"] == "pde-v1"
```

- [x] 注册时 `load_family_dataset` 核验真实包；模型 metadata 和实际模型卡的
  target/task/endpoint/units、model_contract_key、source_sha256（适配源）、dataset_sha256
  （对应子 manifest）、prepared_dataset_sha256 必须匹配对应数据契约；保留真实来源范围。
  不能只比较 seed 或样本数。两模型必须不同、不得 demo/fallback/legacy。
- [x] 现有注册表升级版本，兼容 v1/v2，保留 legacy/endpoint 选择，新增
  `family_bundles` 与 `active_family_bundles`。沿用同一事务与原子写入，不能另造双文件事务。
- [x] 一个 bundle 固定两个权重及卡摘要、数据描述摘要、来源/分配摘要、5/0.5阈值。
  重复 bundle ID 拒绝覆盖；激活时重新校验两模型；任何失败都保留原活动包。
  数据描述原始 JSON 文本保留为固定证据，serving 从该文本独立重算摘要并核对解析内容，
  不能直接复用待验证摘要完成自比较，也不能把原始 CSV 塞进注册库。
- [x] 完整数据验证在 registry 事务外完成；进入事务后读取最新 state，验证当前模型卡、
  权重及重复 ID 后单次写入。事件屏障测试覆盖源预检暂停时已有读取不阻塞、
  并发同 ID 注册仅一方成功，以及预检期间模型删除导致注册失败而非引用悬空。
- [x] 查询活动包返回 detached dict，含 `bundle_id`、`family_id`、`models` 两份完整元数据、
  `source_sha256`、`assignment_sha256`、`scope`、阈值；损坏抛错，无包返回 None。
  单模型删除在同一事务清除依赖 bundle 和活动映射；不影响另一家族。
- [x] GREEN：同家族/跨家族、互换任务、改数据/阈值、坏文件、迁移、失败不半切换、
  删除、重启读取、返回值修改不能污染状态。复跑原 registry 与 endpoint 测试。

## Task 2：固定快照加载与两阶段推理

Files: 新增 `src/activity/family_predictor.py`、`tests/test_activity_family_predictor.py`、
`tests/test_activity_family_inference_integration.py`；
只在必要时给现有 predictor 加可复用加载钩子，不改变默认 legacy 调用。

- [x] RED：实现前先测试类不存在；受控合成阶段返回值验证顺序、失败和数值隔离。

```python
predictor = FamilyActivityPredictor(registry)
rows = predictor.predict(["CCO", "CC(C)(("], target="PDE5A")
assert rows[1]["status"] == "failed"
assert rows[1]["predicted_pIC50"] is None
```

- [x] 缺家族/双家族/无模型明确失败；invalid/multifragment 先校验，不能计算或生成卡片。
  只为有效母体加载模型。每次请求固定一个已验证包，两个阶段不能中途换成不同包。
- [x] 固定模型加载从安全字节快照核验 SHA 后 `torch.load(..., weights_only=True)`，
  不做不安全 pickle fallback；沿用现有 RGNN 构造、图特征及任务输出逻辑。
  不启用默认路径扫描。缓存有界，以家族、bundle、两权重/卡摘要绑定，损坏时不使用旧缓存。
- [x] 先分类再回归，分类成功的全部有效分子（包括阴性）进入回归；分类失败则该行失败，
  回归失败保留分类、status partial、success false、回归 null。概率不是回归值。
  校验阶段输出数量、SMILES 对齐、类型、有限值、任务/单位；不接受 demo/fallback。
- [x] 输出请求靶点、family/bundle、status/success、activity_class/probability、
  predicted_pIC50、5/0.5阈值、classification_regression_consistent、warnings、阶段错误，
  两个 model_id/摘要和来源范围。矛盾输出保持原值，不裁剪也不造数。
- [x] GREEN：概率0.5与标签5边界、矛盾输出、阴性回归、partial、batch对齐、
  家族切换隔离、缓存失效、缺失/损坏模型、恶意输出、真实 RGNN 合成权重工程烟雾检查。
  合成权重不能作为真实科研性能或正式模型发布。

## Task 3：集成审查与交接

Files: 新增 `docs/activity_family_models.md`，更新本计划和
`docs/handoff/activity-data-readiness.md`、`docs/activity_model_registry.md`。

- [x] 规范审查和质量审查；先修复失败回归，再执行完整聚焦命令。

```powershell
python -m pytest tests/test_activity_family_models.py tests/test_activity_family_predictor.py tests/test_activity_family_inference_integration.py tests/test_activity_model_registry.py tests/test_activity_endpoint_selection.py tests/test_activity_prediction_contract.py tests/test_activity_prepared_training_data.py tests/test_activity_prepared_training_loop.py -q -p no:cacheprovider --tb=short
python -m compileall -q src scripts
git diff --check
```

- [x] 不读取密钥，不重复消费真实 test，不训练/激活真实模型，不重启服务。
  用临时合成产物验证注册及前向链路；如依赖缺失如实记录 skip，不伪装真实模型通过。
- [x] 精确暂存代码、合成测试、文档并提交；保持 `codex/activity-data-readiness`，不合并 main。
  原始混杂工作树、私有源数据与上一阶段数据包不变。

最终八模块 334 passed、3 skipped、1 warning；编译和 diff 检查通过。
完整结果及已修复的失败见 `docs/handoff/activity-data-readiness.md`。
