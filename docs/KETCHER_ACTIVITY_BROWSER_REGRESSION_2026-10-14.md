# Ketcher 与活性模型可用性修复回归

日期：2026-10-14。范围：本地浏览器阻塞项的后续修复，不替代全站验收。
分支：`fix/browser-e2e-interactions`；基于 `1af99deb` 继续修复。

发布范围说明：维护者随后确认只将 `e2b816a0` 移植到最新 `main`，发布分支为 `codex/publish-ketcher-browser-fix`，不包含原分支其他 14 个提交。下文浏览器截图、33 项 Node 测试及清空后状态文字复位均是原任务分支的验收记录；状态文字复位属于前序提交，不随本次独立发布。发布分支设计测试为 27 项状态测试 + 5 项就绪测试，另有前端静态契约。其余移植验证以对应 PR 的最新检查记录为准。

## 结果

- Ketcher iframe 正常加载；真实浏览器完成导入、连接点识别、氟取代、属性计算、保存、历史 CSV 下载、候选恢复和清空。
- 按维护者明确授权，将现有 r7 PDE/BuChE 分类与回归模型组启用到本机运行时。单分子及批量请求均得到真实推理结果，非法输入不生成分数。
- 模型可用性恢复不等于科学质量门槛通过。原搜索报告的 `eligible=false` 没有被修改；分类/回归冲突仍显示“需复核”。

## 根因与最小修复

### Ketcher

旧 iframe URL 的浏览器缓存仍携带拒绝嵌入的旧响应。相同资源在新 origin 和顶层页面可正常加载。为 HTML 入口增加 `Cache-Control: no-cache`，并为 iframe URL 加版本号，保留 `X-Frame-Options: SAMEORIGIN` 和 CSP `frame-ancestors 'self'`；其他路径继续禁止嵌入。

本仓库 Ketcher 3.7 的 `setMolecule()` 会在异步画布载入完成前返回，不能把 Promise 完成视为导入完成。桥接层改为等待 `Load canvas` 事件，再读取真实导出结构；导入期间暂停轮询、串行化写入，并暂时将 iframe 设为 inert。超时后禁止继续写同一 iframe，避免旧载入事件完成新请求。

未编号的 `*` / `[*]` 在该版本中会形成无法导出的 R-group。仅为这类占位符补充未使用的连接点编号；已有 atom-map、同位素、立体标记及 CXSMILES 尾部保留。超过支持范围时明确拒绝，不改变结构。

### 设计保存/导出

真实链路额外暴露两个问题：

1. 两个路由要求服务端匿名会话，但入口中间件未覆盖它们，始终返回 401。仅为设计页面、保存和导出入口接入既有会话中间件；跨源拒绝、服务端身份和存储目录隔离不变，无状态设计 API 仍无需会话。
2. 保存校验把 `molecular_formula` 当作浮点数，导致前端回传完整计算属性时失败。分子式改为与服务端重算结果进行严格文本一致性校验；数值属性仍保留有限性与一致性检查。CSV 仍由服务端重算值生成。

独立审查另发现路由模块导入时会创建真实保存目录。删除重复的导入期初始化，保留路由注册阶段的私有目录检查；测试覆盖导入不修改目录的约束。

### 活性模型

默认模型目录没有活动家族模型组；真实训练产物存在于隔离的 r7 目录，问题不是缺少推理代码。经过模型注册表、权重/卡片摘要、家族契约及真实推理检查后，按维护者授权部署四个模型及两个活动组：

- `pde-buche-quality-search-20261011-r7-pde-family`
- `pde-buche-quality-search-20261011-r7-buche-family`

部署先在隔离目录验证，再备份原注册表并原子替换活动注册表。原候选目录和质量报告未改变。权重、运行注册表、备份、操作收据与截图仅保留本机，不进入 Git。此代码提交不会自动向其他机器部署模型资产；其他环境仍需单独授权并校验资产。

本机操作记录位于 `tmp/activity-r7-promotion/activation-receipt.json`，旧注册表备份为同目录 `root-registry-before.json`。其中明确记录 `operator_authorized=true`、`deployment_gate_eligible=false`。如需撤回，先暂停活性请求并确认无并发注册表更新，再恢复备份；不要覆盖期间新增的注册信息。

## 真实浏览器证据

使用本机 `127.0.0.1:6001`、实际 Ketcher、RDKit 和 RG-MPNN 权重，通过可见控件操作；没有模拟接口返回或修改页面状态冒充成功。

设计输入为合成回归用例 `c1cc([*])ccc1`，识别 1 个连接点，选择 `-F` 得到 `Fc1ccccc1`。页面显示 MW 96.10、LogP 1.83、QED 0.46，候选数为 1；保存和导出均返回 200。下载 CSV 内容经文件检查包含：

```csv
step,smiles,prop_mw,prop_logp,prop_qed,prop_tpsa,prop_hbd,prop_hba,prop_rotbonds,prop_sa_score,prop_morgan_similarity
1,Fc1ccccc1,96.104,1.826,0.4618,0,0,0,0,1,0.375
```

恢复候选后属性匹配当前结构；清空后画布为空、性质为 `—`、状态“尚未运行”、迭代归零。

活性预测使用 `CCO`、`CCN`、`not-a-smiles` 合成测试输入，批量顺序保持不变。以下是页面展示值，均为模型预测而非实验测定：

| 靶点 | 输入 | 预测 pIC50 | 活性概率 | 页面状态 |
| --- | --- | ---: | ---: | --- |
| PDE | CCO | 3.1592 | 60.1% | 需复核：分类与回归不一致 |
| PDE | CCN | 4.3038 | 35.9% | 完成 |
| BuChE | CCO | 4.3538 | 34.4% | 完成 |
| BuChE | CCN | 5.0076 | 42.3% | 需复核：分类与回归不一致 |
| 两者 | not-a-smiles | 不可用 | 不可用 | 失败：invalid_smiles |

来源面板显示实际 r7 模型组及各模型摘要，`demo_mode=false`、`fallback_used=false`。数据范围仍为混合物种、用户提供数据，实验复现未验证，数据再分发许可未确立；不应把“完成”解释为实验验证或质量认证。

本机截图（不提交）：

- `tmp/ketcher-save-browser-20261014.jpg`
- `tmp/activity-buche-browser-20261014.jpg`

## 自动化验证

在具备 RDKit/PyTorch/PyG 的 MedChat Conda 环境执行：

```powershell
python -m pytest tests/test_design_browser_sessions.py tests/test_agent_session.py tests/test_agent_session_entrypoints.py tests/test_molecular_design_architecture.py tests/test_molecular_design_integrity.py -q
python -m pytest tests/test_security_headers.py tests/test_activity_family_api.py tests/test_activity_model_registry.py tests/test_activity_family_models.py -q
node tests/molecular_design_state_test.js
node tests/molecular_design_editor_ready_test.js
node tests/molecular_design_frontend_test.js
python -m compileall -q src scripts
$env:MEDCHAT_REQUIRE_ACTIVE_ACTIVITY_MODELS='1'
python scripts/health_check.py --strict
git diff --check
```

- 第一组 Python：251 passed、3 skipped、4 warnings；跳过项为平台相关检查。
- 第二组 Python：210 passed、7 warnings。
- JavaScript：状态测试 28/28、就绪测试 5/5、前端静态契约通过；全站非 vendored/backup JavaScript 的 `node --check` 通过。
- 编译和 diff 检查通过；要求活动模型的严格健康检查 23/23 通过。
- 新会话测试在修复前复现 5 个 401 相关失败；分子式测试在修复前复现 4 个失败；Ketcher 新增状态测试先失败后通过。
- 本轮未运行整个仓库 Python 测试全集；上述是聚焦回归，不是全站无缺陷声明。

## 文件范围与剩余限制

实现：`src/web/security/headers.py`、`src/web/agent_session_config.py`、`src/web/routes/design_routes.py`、`src/web/templates/molecular_design.html`、`src/web/static/js/design/molecule_editor.js`、`src/web/static/js/design/main.js`、`src/molecular_design/storage.py`。

回归：`tests/test_security_headers.py`、`tests/test_design_browser_sessions.py`、`tests/test_molecular_design_integrity.py`、`tests/molecular_design_state_test.js`、`tests/molecular_design_editor_ready_test.js`、`tests/molecular_design_frontend_test.js`。

仍有非阻塞告警：Ketcher vendored 包的 `convert` 事件监听数量警告、旧的未注册模型 sidecar 警告，以及 pandas/FastAPI/SWIG 的弃用或未来行为警告。本轮未改动第三方 bundle，也未隐藏这些告警。

审查重点：Ketcher 版本绑定的完成事件、超时后的刷新要求、会话安全边界、模型可用性与质量合格之间的区别。此前全站审计中其他模块的能力缺口不在本轮完成声明内。
