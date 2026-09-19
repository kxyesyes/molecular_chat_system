# PDE/BuChE Isolated Weight Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 集成默认关闭的 PDE/BuChE 真实权重验收入口，以离线合成权重验证 API、逐轮决策、聊天桥接和 DOM 展示链路；本批不读取真实权重或调用外部模型。

**Architecture:** 复用现有注册表验证、双阶段预测器、ModelDecisionLoop、ChatHandler 和 pytest。验收专属代码留在 tests/，分离只读快照、进程所有权、入口断言；科学计算始终由现有推理代码执行。父进程只管理临时目录、子进程和脱敏报告，不读取源模型目录。

**Tech Stack:** Python 3.10、pytest、PyTorch/PyG、RDKit、FastAPI TestClient、Node.js vm/DOM fixture；现有 Windows Job Object / POSIX process group 机制。

---

## 0. 已确认的范围、基线及执行约定

- 设计依据：`docs/superpowers/specs/2026-09-15-family-real-acceptance-design.md`，用户已确认方案A。
- 独立工作树：`D:/MedChat/molecular_chat_system_worktrees/family-real-acceptance-integration`。
- 分支：`codex/family-real-acceptance-integration`；基线 main `57680aa`，设计提交 `56caac6`。
- 原始 `D:/MedChat/molecular_chat_system` 的混杂改动保持原样。禁止在该目录实施本计划。
- 旧 `9312bf5:tests/test_activity_family_real_acceptance.py` 仅作参考，禁止整体移植其 copytree、最后一个 bundle 选择、全环境继承、旧 Supervisor 替代新决策层、JS 源字符串切片等行为。
- 默认不改 src/，不新增公开 API，不改 TaskRequirements，不训练、不选择生产模型、不读取真实 CSV、.env、API key 或真实模型目录。
- 合成权重是未训练 RGNN 的真实前向，不是科研性能测试；报告必须区分 `synthetic_fixture` 与 `trained_weights`，决策模型固定 `scripted`。
- 各任务采用 RED → GREEN → 聚焦回归 → 显式暂存提交；表中未来文件名和函数名是本批要建立的内部测试接口，不声称基线已有这些能力。

所有 PowerShell 命令先进入上述独立工作树，并设置解释器变量（不包含任何凭据）：

```powershell
$py = 'C:/Users/xkx52/.conda/envs/MedChat/python.exe'
git branch --show-current
git status --short
```

普通回归先明确关闭唯一实测开关，不枚举其它环境变量：

```powershell
$env:MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE = '0'
```

## 1. 文件责任与依赖

| 文件 | 新增/修改 | 单一责任 |
|---|---|---|
| `tests/family_real_acceptance_support.py` | 新增 | 配置解析、受限快照、源摘要核对、报告投影 |
| `tests/family_acceptance_process_support.py` | 新增 | 显式环境的受监督子进程、超时与回收；不加载科学模型 |
| `tests/family_acceptance_chain_support.py` | 新增 | 用同一快照运行预测器/API/工具/决策/ASGI/DOM，形成结构化断言记录 |
| `tests/family_model_test_support.py` | 修改 | 提取现有合成 RGNN 权重构造回调，继续保留旧 fixture 公共调用方式 |
| `tests/test_activity_family_inference_integration.py` | 修改 | 调用提取后的 fixture，原有测试不减少 |
| `tests/test_activity_family_acceptance_support.py` | 新增 | opt-in、路径、大小、选择确定性、封存证据、报告负例 |
| `tests/test_activity_family_acceptance_process.py` | 新增 | 环境隔离、真实短子进程、超时/后代/取消/失败回收 |
| `tests/test_activity_family_acceptance_chain.py` | 新增 | 合成权重全链路、目标/输入/阶段/证据负例 |
| `tests/test_activity_family_real_acceptance.py` | 新增 | 默认 skip 的唯一真实入口、两个家族顺序运行及汇总 |
| `tests/activity_family_results_test.js` | 修改 | 显式导出已有 setup/cells/safety fixture，require 不执行旧测试 |
| `tests/activity_family_acceptance_dom.js` | 新增 | 读取子进程提供的有界 API JSON，验证生产 renderer 输出 |
| `docs/handoff/family-real-acceptance-integration.md` | 新增 | 每批实际证据、限制、未来显式运行方式 |
| `docs/handoff/latest.md`、`docs/handoff/historical-integration-status.md` | 修改 | 标记 PR #28 已合并；区分本批代码状态与真实权重未运行 |

依赖顺序：Task1 → Task2 → Task3；Task4 可独立开发；Task5 依赖1–4；Task6依赖5；Task7为整体验证/交接。

不要继续拆成新的运行平台；三个 support 模块分别对应资产、进程、链路，避免把所有内容塞进一个超长测试。

## 2. 本批固定边界

这些是验收入口的资源限额，不是对生产模型规模的承诺。超过边界直接失败；不自动扩大、下载或换模型。

| 项目 | 限额/行为 |
|---|---|
| registry JSON | 16 MiB；严格 JSON，拒绝重复键、NaN/Infinity、深度>32 |
| 单份 model card | 2 MiB；同样严格 JSON |
| 单份权重 | 512 MiB；二进制流式复制/哈希，读取块1 MiB |
| 每家族权重/card | 恰好2份权重+2份card；只取指定bundle关联记录 |
| bundle/model ID | 沿用 registry 的字符/长度校验；两个阶段 model ID 必须不同 |
| 路径 | 源必须显式绝对本地路径；拒绝UNC、设备、ADS、父级穿越、symlink/reparse；所有祖先及叶节点检查 |
| 源访问 | 只允许registry及所选4个资产；不glob、不遍历扫描、不实例化源registry |
| 运行 | 每家族120秒；Node30秒且不超过家族剩余时间；清理另设有界期限并单独记录 |
| 子进程输出 | 复用 CommandAdapter 有界输出捕获；stdout/stderr不直接转发或写报告 |
| 家族中间报告 | 1 MiB、JSON深度16、容器最多128项、单字符串最多512字符 |
| 总报告 | 2 MiB；超限/截断必要证据不能判passed |
| 浮点 | 有限实数，拒绝bool；概率[0,1]；CPU abs/rel tolerance均1e-6 |
| 文本展示 | pIC50四位小数、概率沿现有renderer百分比规则；缺失值不是0 |

报告和临时文件均排除提交。原始模型metadata/card可在临时副本保留供验证，不把全部metadata发布到报告。上述大小/类型/hash门禁不是不可信权重的完整安全沙盒；即使restricted load也不证明来源可信，只接受用户明确指定且信任的资产。

### 内部接口约定

```python
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True, repr=False)
class AcceptanceConfig:
    source: Path
    pde_bundle_id: str
    buche_bundle_id: str

@dataclass(frozen=True, repr=False)
class FamilySnapshot:
    models_dir: Path
    family_id: str
    bundle_id: str
    expected_models: dict
    source_digests: dict

@dataclass(frozen=True)
class ChildResult:
    status: str
    reason: str | None
    exit_code: int | None
    report: dict | None
    ownership_released: bool
    cleanup_complete: bool
```

`read_config(env)`返回AcceptanceConfig或None，仅解析字符串，不做文件系统访问。
`snapshot_family(config, family_id, destination)`仅在已授权子进程或合成fixture测试中调用。
`verify_source(config, snapshot)`返回受限逻辑文件摘要是否一致；失败抛公开验收错误，不泄漏路径。
`run_owned_child(argv, *, env, cwd, timeout, cancel_event=None)`返回ChildResult；不使用shell。
`run_family_chain(snapshot, *, work_dir, mode)`返回未发布的家族结构化报告。
`public_report(report)`严格字段投影后调用现有sanitize_bounded；`write_report(path, report)`复用现有open_report独占新建。

公开验收错误仅使用固定码：`invalid_configuration`、`unsafe_source`、`asset_limit_exceeded`、`invalid_registry`、`bundle_mismatch`、`asset_digest_mismatch`、`source_changed`、`child_timeout`、`child_failed`、`ownership_uncertain`、`invalid_report`、`dependency_unavailable`、`chain_mismatch`。不得附加原始异常对象。

## Task 1: 默认关闭的配置与合成资产fixture

实施完成（2026-09-19）：`8835cee`、`0fd493b`、`e4a8f24`；SPEC与QUALITY复审均通过，最终135 passed。详细RED/GREEN及两处审查修复见本批交接。

**Files:** 新增support及其单测；修改现有两个family fixture/inference文件。

- [x] **Step 1 — 写默认关闭和显式选择的RED测试。** 单测使用显式dict，不读取本机模型配置。证明关闭时连其它字段也不访问：

```python
def test_disabled_gate_does_not_read_other_configuration():
    from tests.family_real_acceptance_support import read_config
    class Disabled(dict):
        def get(self, key, default=None):
            assert key == 'MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE'
            return '0'
    assert read_config(Disabled()) is None

def test_enabled_requires_both_explicit_bundle_ids():
    import pytest
    from tests.family_real_acceptance_support import read_config
    with pytest.raises(ValueError, match='invalid_configuration'):
        read_config({'MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE': '1'})
```

另参数化`''/0/true/yes/1`，只有字符串`'1'`启用；缺一个ID、同一ID、非法ID、相对/UNC源路径失败。关闭时patch Path.stat/open抛错仍应通过，证明未触文件系统。

- [x] **Step 2 — 运行RED。**

```powershell
& $py -B -m pytest tests/test_activity_family_acceptance_support.py -q -p no:cacheprovider --tb=short
```

预期因待建模块/接口缺失失败；保留失败原因，不当作真实依赖问题。

- [x] **Step 3 — 实现纯配置解析，提取现有真实RGNN合成构造。** 不复写scientific predictor。将现有inference测试中seed71、channels8、CPU、真实torch.save逻辑提取为`make_forward_bundle(tmp_path, monkeypatch, *, family, bundle_id)`，返回`(registry, models)`；生成package ID和model前缀随family区分。使用`torch.random.fork_rng`，恢复线程数，所有文件在tmp_path。

```python
def test_forward_fixture_is_temporary_and_has_no_global_selection(tmp_path, monkeypatch):
    from tests.family_model_test_support import make_forward_bundle
    registry, models = make_forward_bundle(
        tmp_path, monkeypatch, family='PDE', bundle_id='synthetic-pde')
    assert registry.models_dir.is_relative_to(tmp_path)
    assert registry.get_active_model_id() is None
    assert set(models) == {'classification', 'regression'}
```

离线chain fixture只接受测试合成的目录，强制禁用全局模型fallback、CSV重开和网络transport；CPU设备是显式选择。不要用生成fixture前的ActivityPredictor全局加载去发现磁盘模型。

- [x] **Step 4 — GREEN及旧fixture回归。**

```powershell
& $py -B -m pytest tests/test_activity_family_acceptance_support.py tests/test_activity_family_inference_integration.py tests/test_activity_family_models.py -q -p no:cacheprovider --tb=short
```

- [x] **Step 5 — 显式提交该批文件。** `test: add isolated family acceptance configuration fixtures`。

## Task 2: 源只读快照、精确bundle和证据核对

实施完成（2026-09-19）：`321c858`、`35d9d09`、`6022c6e`；独立双审通过，306 passed、3 skipped（Windows symlink权限）、2 warnings。

**Files:** support、support单测。

- [x] **Step 1 — 写RED。** 用Task1合成源注册两个同家族bundle，明确选择较早的一组；源原本active选择另一组。以下断言禁止默认/最后选择：

```python
def assert_snapshot_selection(snapshot, selected_id, expected_models):
    assert snapshot.bundle_id == selected_id
    for task in ('classification', 'regression'):
        assert snapshot.expected_models[task]['model_id'] == expected_models[task]['model_id']
        assert snapshot.expected_models[task]['weights_sha256'] == expected_models[task]['weights_sha256']
        assert snapshot.expected_models[task]['model_card_sha256'] == expected_models[task]['model_card_sha256']
```

同时断言副本仅5个初始文件（registry+2weights+2cards），源摘要和源active选择不变；source registry构造/源write-open/CSV-open用测试guard直接抛错。副本随后允许产生锁/状态文件。

参数化负例：错家族、缺阶段、重复model ID、未知bundle、registry/card重复JSON键、unsupported version、坏hash、源变化、大小超限、目录代替文件、`../`、Windows反斜线、ADS、设备名、symlink/reparse、共享文件名/大小写碰撞。超限用小测试常量或稀疏临时文件，不分配512MiB数组。

- [x] **Step 2 — 运行Task2测试为RED。** 使用Task1相同support测试命令，确认新增边界失败。

- [x] **Step 3 — 实现快照核心。** 先受限只读严格解析registry，获取指定bundle完整封存记录，再取得两个selected model records；拒绝无关联资产引用。复制每个文件使用独占新建、读取前lstat和打开后fstat、边读边限额/摘要、读后身份复核。不要先resolve再检查symlink，否则会丢失链接信息。

副本state精确投影为当前version3：

```python
def minimal_state(bundle, selected_models):
    from copy import deepcopy
    return {
        'version': 3,
        'models': deepcopy(selected_models),
        'active_model_id': None,
        'active_models_by_endpoint': {},
        'family_bundles': {bundle['bundle_id']: deepcopy(bundle)},
        'active_family_bundles': {},
    }
```

随后**仅在副本**调用ActivityModelRegistry与select_family_bundle，使现有权重/card/数据封存证据一致性校验真正执行；不调用register_family_bundle重开数据。复制前后及完成前核对源registry/四资产摘要；最终报告用`registry`、`classification.weights`等逻辑名。

- [x] **Step 4 — GREEN。** support+family_models+family_predictor+inference回归；Linux必须实际覆盖symlink，Windows不可创建链接时单列skip，reparse属性负例仍运行。
- [x] **Step 5 — 显式提交。** `test: pin and isolate selected family acceptance assets`。

## Task 3: 环境白名单、子进程所有权与超时

实施完成（2026-09-19）：`1dcc610`；SPEC/QUALITY均通过，50 passed、1 skipped。Windows真实短进程验证完成，Linux执行保留为Task7 CI门禁。

**Files:** process support、process tests；support报告错误映射。

- [x] **Step 1 — 写RED。** 以短Python子进程验证允许环境；只使用synthetic值，禁止枚举/传递真实key：

```python
def test_child_environment_is_an_allowlist(tmp_path):
    from tests.family_acceptance_process_support import child_environment
    source = {'SystemRoot': 'synthetic-system', 'PATH': 'synthetic-path',
              'UNRELATED_PROVIDER_SECRET': 'synthetic-secret', 'ACTIVITY_MODEL_DIR': 'wrong'}
    result = child_environment(source, tmp_path)
    assert 'UNRELATED_PROVIDER_SECRET' not in result
    assert 'ACTIVITY_MODEL_DIR' not in result
    assert result['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] == '1'
    assert result['PYTHONDONTWRITEBYTECODE'] == '1'
    assert result['HOME'] == str(tmp_path)
```

OS继承只读取SystemRoot/WINDIR/PATH/PATHEXT/SYSTEMDRIVE五项；TMP/TEMP/TMPDIR/HOME/USERPROFILE/XDG_CACHE_HOME由父临时目录覆盖；设置UTF-8、CPU线程1、CUDA_VISIBLE_DEVICES空、PYTHONNOUSERSITE=1。Node使用父进程解析出的明确可执行路径，不从用户请求拼命令。实测配置只传所选source/family/bundle，不传API相关变量。

真实短子进程测试：正常退出、非零退出、睡眠超时、派生睡眠后代、主进程先退出而后代仍运行、取消、输出超限；断言无存活的本次后代、错误码和临时清理。不得只mock subprocess就声称回收有效。

- [x] **Step 2 — 跑RED。**

```powershell
& $py -B -m pytest tests/test_activity_family_acceptance_process.py -q -p no:cacheprovider --tb=short
```

- [x] **Step 3 — 建立验收专属薄进程适配。** 复用`src/docking/adapters/base.py`的Job Object挂载/受限捕获/终止回收原语，不调用docking工具、不修改生产适配器、不新增进程管理平台。Windows以测试局部Popen factory传env，先挂载suspended进程再resume；POSIX使用Popen(env=白名单,start_new_session=True)和现有capture/group cleanup。禁止全局patch Popen或临时清空父os.environ。

Windows环境注入的复用点明确为现有可注入工厂：

```python
def windows_spawn(args, cwd, env):
    import subprocess
    from src.docking.adapters.base import CommandAdapter
    def factory(*args, **kwargs):
        return subprocess.Popen(*args, env=env, **kwargs)
    return CommandAdapter._create_windows_suspended(args, cwd, popen_factory=factory)
```

监督循环必须把启动、执行、输出采集、子孙进程状态纳入deadline；已有`_create_windows_until_control`在超时后可能尚有spawn worker，不可直接把抛TimeoutExpired当作资源已释放。新增测试必须覆盖late-create与清理确认。未证明所有权释放时，报告`ownership_uncertain`且拒绝执行payload/标记成功；不得边后台执行边删除工作目录。若复用原语无法满足要求，停在RED说明实际阻塞，不扩大生产权限或隐瞒为普通timeout。

失败报告不包含stdout/stderr/raw exception。CPU子进程异常、Node超时/缺失、报告缺失都不能通过。KeyboardInterrupt同样进入已拥有进程回收；清理失败单独failed，保留临时目录是故障证据而非成功清理。

- [x] **Step 4 — GREEN。** process测试分别在Windows和Linux实际执行；已有CommandAdapter相关测试随全回归一起验证。只在确认owned tree回收后清理父目录。
- [x] **Step 5 — 显式提交。** `test: supervise isolated family acceptance processes`。

## Task 4: 显式复用生产renderer的DOM测试fixture

实施完成（2026-09-19）：`fee957b`；SPEC/QUALITY均通过，旧18/18、新15/15，两个语法检查通过；父级已通过Task3监督执行Node驱动。

**Files:** `tests/activity_family_results_test.js`、新增DOM驱动。

- [x] **Step 1 — 写RED。** 独立Node驱动require现有test文件，要求setup/cells/assertNoExecutableNodes导出；require不能自动跑旧18项测试或写stdout。

```javascript
const assert = require('node:assert/strict');
const {setup, cells, assertNoExecutableNodes} = require('./activity_family_results_test.js');
assert.equal(typeof setup, 'function');
assert.equal(typeof cells, 'function');
assert.equal(typeof assertNoExecutableNodes, 'function');
```

- [x] **Step 2 — 运行RED。** `node tests/activity_family_acceptance_dom.js`的self-test路径应因缺少导出失败；不读取实际模型。
- [x] **Step 3 — 给已有执行循环加main guard并导出fixture。**

```javascript
module.exports = {setup, cells, assertNoExecutableNodes};
// Existing assertions remain registered; run the existing async test loop only
// under require.main === module, retaining its nonzero exit on failure.
```

DOM驱动默认self-test；指定`--input`时只读已存在的子进程中间JSON，最大1MiB，不接受JS代码/任意执行参数。生产render调用使用实际summary.results和summary.status/success/warnings，断言row顺序和返回来源，不取全局表单选中模型。

```javascript
function assertDisplayedPrediction(row, expected) {
  const shown = cells(row);
  assert.equal(shown[1], Number.isFinite(expected.predicted_pIC50)
    ? expected.predicted_pIC50.toFixed(4) : '不可用');
  assertNoExecutableNodes(row);
}
```

保留所有旧partial/null/zero/XSS断言；新增实际API rows的概率、类别、模型ID/hash存在断言。负例用恶意字符串仅验证惰性展示，不给真实计算结果换标签。

- [x] **Step 4 — GREEN。**

```powershell
node tests/activity_family_results_test.js
node tests/activity_family_acceptance_dom.js
node --check tests/activity_family_results_test.js
node --check tests/activity_family_acceptance_dom.js
```

- [x] **Step 5 — 显式提交。** `test: expose family result DOM fixture for acceptance`。

## Task 5: 同一快照的真实前向、API、逐轮决策与WebSocket

实施完成（2026-09-19）：`74d84f0`、`82f2efe`、`a5409df`；SPEC/QUALITY复审通过，六组255 passed、2 warnings；四项验收漏检及修复证据保留在交接。

**Files:** chain support及单测；Task1–4 support按内部接口调用。

- [x] **Step 1 — 编写链路RED测试。** 参数化PDE/PDE5A和BuChE/BChE，Task1合成真实前向，Task2创建临时副本；指定本测试ACTIVITY_MODEL_DIR后依次执行各入口。基准固定`['CCO', 'CCN', 'CCO']`，另外独立测试无效输入`CC(C)((`和未知靶点`AChE`。

每条有效行必须比较status/family/bundle/两阶段model和hash/阈值/类别/warnings/errors；核心数值校验：

```python
def assert_numbers(actual, expected):
    import math
    import pytest
    for field in ('activity_probability', 'predicted_pIC50'):
        value = actual[field]
        assert type(value) in (int, float) and math.isfinite(value)
        assert value == pytest.approx(expected[field], abs=1e-6, rel=1e-6)
    assert 0 <= actual['activity_probability'] <= 1
    assert actual['label_threshold'] == 5.0
    assert actual['probability_threshold'] == .5
```

API必须实际FastAPI setup_api_routes+TestClient，单条`/api/activity/predict`及multipart`/api/activity/batch_predict`；不能替换handler返回值。未知target与invalid返回failed/null；批量保序、重复不丢；将实际summary交Task4 Node驱动。

- [x] **Step 2 — 跑RED。**

```powershell
& $py -B -m pytest tests/test_activity_family_acceptance_chain.py -q -p no:cacheprovider --tb=short
```

- [x] **Step 3 — 实现严格脚本决策模型。** 使用现有ToolDecision/FinishDecision/DecisionResponse，不加入预测数值；第二轮从真实observation取evidence_id。测试专属model只复制Task API必要的短逻辑，不引入对`test_decision_loop`的脆弱顶层import路径依赖。

```python
def activity_decision():
    from src.agent.contracts.decision import ToolDecision
    return ToolDecision(version='1', action='tool', tool_name='activity_predictor',
                        arguments={'input_ref': 'user'}, purpose='predict_activity')

def finish_observed(messages):
    import json
    from src.agent.contracts.decision import FinishDecision
    observation = json.loads(messages[-1]['content'])
    return FinishDecision(version='1', action='finish', response_kind='scientific',
                          text='Scripted decision; not scientific prose.',
                          evidence_ids=[observation['quality']['evidence_id']])
```

真实ActivityPredictorTool注册到build_tool_registry，新ModelDecisionLoop+临时SQLiteAgentStateStore，context明确本次target/SMILES，allowed_tools=required_tools={'activity_predictor'}。Spy包裹实际execute保留完整行为和输入记录，不替换scientific结果；patch静态Planner和旧global predictor为fail守卫。必须看到两轮模型调用、第二轮观察来自刚执行的tool、metadata.backend=model_decision_loop、最终答案数值来自tool。

- [x] **Step 4 — 实现ChatHandler显式桥接与in-process ASGI。** 禁止访问生产app全局启动。ForbiddenLegacy传入ChatHandler；测试app WebSocket endpoint调用process_decision_message，显式授权activity工具；新建独立trace和store防旧证据重用。兼容旧CI Starlette，不用TestClient(client=...)。

终结断言：

```python
def assert_terminal(messages, trace_id):
    complete = [item for item in messages if item['type'] == 'complete']
    result = [item for item in messages if item['type'] == 'agent_result']
    assert len(complete) == len(result) == 1
    assert complete[0]['trace_id'] == trace_id
    assert messages[-1]['type'] == 'complete'
    assert not any(item['type'] == 'molecular_generation' for item in messages)
    assert result[0]['tool_result_sequence']
```

额外核对event序列task_started/planning/tool/terminal、工具provenance输入摘要、warnings/errors/status和最终答案；valid为completed，invalid为failed/rejected，不能出现分子可视化或无证据pIC50。`AgentResult.status`与验收status分别保留，不强求所有层枚举字符串相同。

- [x] **Step 5 — 新增故障RED后最小补全断言。** 在临时副本做缺权重/坏card/hash/缺阶段；在synthetic service边界注入跨family/跨SMILES/空成功/demo/fallback/wronghash行。它们只能是负例，不能计入真实前向成功数。错误应保留原公开码和已有阶段；禁止把MODEL_UNAVAILABLE当预期invalid SMILES通过。

续接负例使用ClarifyDecision →本次修正输入→实际tool，证明旧目标/旧证据不污染当前结果；越权tool决策被拒绝且execute次数0。若生产行为已正确只补测试；如发现真实生产缺陷，保留RED并按设计升级范围，不静默改公共契约。

- [x] **Step 6 — GREEN并关闭缓存/registry/store。**

```powershell
& $py -B -m pytest tests/test_activity_family_acceptance_chain.py tests/agent/test_family_activity_tool.py tests/agent/test_decision_chat.py tests/agent/test_decision_chat_transport.py tests/test_activity_family_api.py tests/test_activity_family_inference_integration.py -q -p no:cacheprovider --tb=short
```

- [x] **Step 7 — 显式提交。** `test: exercise family inference through isolated decision chat`。

## Task 6: 唯一opt-in入口与可信报告

**Files:** real acceptance入口、support/report单测；复用process+chain。

- [ ] **Step 1 — 编写入口和报告RED。** 默认关闭时test函数在任何源stat/registry导入之前pytest.skip；enabled配置错误failed而非skip。离线测试以synthetic环境调用read_config/child runner，不从宿主读取真实配置。

真实入口使用单个pytest项顺序运行两个家族并汇总；一个失败不把另一个标成未执行的成功。两个child各自只复制两阶段资产，不递归再次启动pytest。child worker使用`python -B -m tests.family_acceptance_chain_support`明确内部参数，所需source路径和bundle只在运行时传递；禁止写参数快照日志。

实施接口补充（2026-09-19，只涉及tests内部）：已用临时空目录与白名单环境复现`-m tests...`无法定位仓库模块。Task6允许给`run_owned_child`增加可选`environment_dir`（默认仍为cwd，兼容Task3），让worker的cwd为仓库、HOME/TMP等指向父级拥有的临时目录。需先补真实短进程RED并验证这两个目录各自用途；不继承PYTHONPATH、不复制仓库、不修改生产接口。Task6独立审查需覆盖此增量。

报告检查独立字段：

```python
def assert_report_scope(report, mode):
    assert report['mode'] == mode
    assert report['decision_model_kind'] == 'scripted'
    assert report['scope']['external_model'] == 'not_run'
    assert report['scope']['production_selection'] == 'unchanged'
    assert report['status'] in {'passed', 'partial', 'failed', 'skipped'}
```

每个case记录expected/actual身份摘要、入口、actual_tools、trace/events、latency_ms、checks、result_status、公开error_code、warnings、available_stages。没有artifacts则空列表，不能伪造pose或分子结果。持久化Agent状态在临时目录，报告只记录逻辑引用，不导出原始聊天/内部模型消息。

- [ ] **Step 2 — 跑RED。** support/process/chain测试加入口默认关闭测试；真实测试预期1 skipped，不能称真实passed。

- [ ] **Step 3 — 实现报告投影和聚合。** 复用`src/agent/persistence/redaction.py:sanitize_bounded`和`scripts/run_decision_chat_acceptance.py:open_report`，导入后不得执行main或provider配置读取。保留逻辑ID和hash，不投影任意模型metadata。sanitize发生必要证据截断/缺失时report failed，不删除失败再算通过率。

成功条件是全部必需case通过、source复核通过、子进程exit0、所有权已释放、临时清理完成；清理/源复核状态由实际完成方填写。一个family失败另一个通过为partial；全部失败为failed；显式关闭为skipped。预期拒绝的case验收passed仍保留scientific result_status failed/rejected。`production_selection=unchanged`只用于源状态复核通过的报告；复核未完成为`not_verified`，源状态变化为`changed`，不能在失败报告中预填unchanged。

Task3内部协议区分传输与科学状态：成功收集的信封为`{status: "passed", scientific_report: {...}}`，内层科学报告仍可failed/partial并保留source_check。`ChildResult.status`不是科学通过依据；顶层failed仅记录传输错误，不返回未可信报告。

输出用新UUID文件名`outputs/agent_evaluation/family_acceptance_<id>.json`、allow_nan=False，禁止覆盖已有文件；只写完成投影的有界结果。不传递原始stdout、错误堆栈、环境全集、source绝对路径或私有训练元数据。异常/超时缺子报告时父仅写固定错误与`source_check=not_completed`，不读源补验。

- [ ] **Step 4 — GREEN及脱敏负例。** 注入synthetic路径/secret风格文本/超长字段/NaN/大数组，证明拒绝或脱敏；报告已有目标/链接路径拒绝；异常后目录清理检查。只检查已知测试常量，不扫描真实key。
- [ ] **Step 5 — 显式提交。** `test: add opt-in trained family acceptance reporting`。

## Task 7: 全回归、独立审查、交接与PR

**Files:** 本计划状态、设计状态、三份交接/台账；必要的本批修正。

- [ ] **Step 1 — 聚焦回归。** 本地仍设置`MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE=0`：

```powershell
& $py -B -m pytest tests/test_activity_family_acceptance_support.py tests/test_activity_family_acceptance_process.py tests/test_activity_family_acceptance_chain.py tests/test_activity_family_real_acceptance.py tests/test_activity_family_inference_integration.py tests/test_activity_family_api.py tests/test_activity_family_models.py tests/test_activity_family_predictor.py tests/agent/test_family_activity_tool.py -q -p no:cacheprovider --tb=short
```

预期离线工程测试通过，唯一真实项明确skip；记录实际数量，不预填通过数。

- [ ] **Step 2 — Agent及已有model/fallback联合回归。**

```powershell
& $py -B -m pytest tests/agent tests/test_agent_decision_model.py tests/test_openai_compatible_model.py tests/test_agent_anti_hallucination_fallbacks.py tests/test_agent_platform_health_check.py tests/test_admet_predictor_fallback.py -q -p no:cacheprovider --tb=short
Get-ChildItem tests -File -Filter '*test.js' | ForEach-Object { node $_.FullName; if ($LASTEXITCODE -ne 0) { throw 'Node regression failed' } }
node tests/activity_family_acceptance_dom.js
node --check tests/activity_family_results_test.js
node --check tests/activity_family_acceptance_dom.js
$env:PYTHONPYCACHEPREFIX = Join-Path ([IO.Path]::GetTempPath()) ('mc-' + [guid]::NewGuid().ToString('N').Substring(0,8))
& $py -m compileall -q src scripts
& $py -B scripts/run_agent_acceptance.py --mode contract
git diff --check
```

现有10个Node脚本都保留；新DOM驱动额外执行。compile缓存需在确认目录位于系统temp且进程已退出后清理，不使用未经检查的递归删除。不得运行real/all模式或带实际模型配置入口。

- [ ] **Step 3 — 独立规格审查，再独立质量审查。** 提供精确head、指定MedChat解释器、该设计/计划、原始工作树禁改边界、实际RED/GREEN记录。重要发现必须最小修复后重测；CI缺依赖/平台特定失败如实保留，不重复重跑冒充修复。

- [ ] **Step 4 — 更新台账。** PR #28确认为已合并57680aa、CI34950563640全7项；保留初次Starlette CI失败记录。本批状态只能写“代码集成/离线合成验收”，另列“真实训练权重、外部主模型、真实浏览器、生产部署未执行”。历史报告展示残差仍是另一批，不因这次完成而关闭。

- [ ] **Step 5 — 发布前检查并显式暂存。** 确认没有weights/CSV/JSON运行报告/env/log/temp进入diff，列出实际文件后逐项git add。分支始终不是main；检查原始脏工作树未变化。
- [ ] **Step 6 — 创建单主题draft PR到main，检查最新head Linux CI及审查意见。** CI缺少新测试时必须修正测试收集/命令范围再重跑，不能仅凭旧suite绿灯通过。仅在用户明确指定该PR授权后合并，不推断本次“确认”是未来PR合并授权。

## 自检映射与实施交接

| 已批准设计要求 | 实施任务 |
|---|---|
| 默认关闭、精确两家族bundle、无源registry副作用 | 1、2、6 |
| 有界输入、路径/哈希/封存证据、只复制选中资产 | 2 |
| 子进程所有权、环境过滤、超时/取消/Node清理 | 3、4、6 |
| 真实前向、API/工具/新决策/ASGI/DOM分别验证 | 4、5 |
| 非伪造/partial/无效输入/错目标/续接/越权 | 2、5、6 |
| 结构化脱敏、原结果状态、资源退出与未执行范围 | 6 |
| TDD、现有回归、双审、独立PR与明确合并授权 | 1–7 |

- [x] 用户已批准设计，计划基于当前真实接口编写。
- [x] 已固定资源限额、内部接口、文件范围、命令及每阶段失败标准。
- [x] 已核对没有用旧Supervisor或合成前向替代真实训练权重验收。
- [x] 已明确Windows late-create/所有权不确定不能冒充正常清理。
- [x] 选择执行方式：用户选择1，子代理逐任务开发/两阶段审查。
- [ ] 实施各Task并将实际RED/GREEN、提交及CI结果写入交接。

本文件是实施计划，不是整体验收完成声明。Task1–2已实施并通过双审，Task3正在实施；后续各项以勾选状态及交接证据为准。未读取真实权重，未调用外部模型。
