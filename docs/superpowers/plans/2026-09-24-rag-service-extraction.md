# T11-A RAG 服务归位实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox syntax for tracking.

**Goal:** 让领域RAG服务可独立导入，不依赖Web应用组装，旧入口继续导出同一个实现。

**Architecture:** 将既有类及索引模块原样迁移到 `src/rag`，Web只导入/导出，无包装子类或第二份检索逻辑。已有T01共享检索改为领域索引导入。用实际FAISS、工厂、ChatHandler回归保护行为。

**Tech Stack:** Python 3.10、FAISS、NumPy、Pandas、HTTPX、pytest。

## 文件职责与写集

- 新增 `src/rag/service.py`：唯一RAGSystem。
- 新增 `src/rag/index.py`：唯一manifest/索引持久化实现。
- 修改 `src/web/app.py`：删除内嵌RAGSystem，导入规范类及清除仅类使用的导入。
- 修改 `src/web/rag_index.py`：显式兼容导出。
- 修改 `src/rag/retrieval.py`：仅替换索引导入路径。
- 修改 `tests/test_rag_index_manifest.py`：规范服务导入与真实竞争/原子写入patch路径。
- 修改 `tests/test_agent_platform_health_check.py`：新canonical路径加旧入口identity。
- 新增 `tests/test_rag_service_boundary.py`：独立导入、公共对象identity与组装边界。
- 本计划和对应spec记录设计/真实结果；不新建重复handoff。

## 任务1：RED，验证新边界不存在

- [ ] 新增隔离子进程测试，使用白名单环境、临时cwd和明确repo sys.path，设置PYTHONDONTWRITEBYTECODE，执行下列核心断言。用超时上限防挂起但不当性能SLA；捕获输出仅用于断言失败，不输出环境。子进程阻止socket连接，不初始化服务、不读取真实CSV/索引，不导入全局app。

```python
import sys
from src.rag.service import RAGSystem
from src.rag.index import RAGIndexManifest
assert RAGSystem.__module__ == 'src.rag.service'
assert RAGIndexManifest.__module__ == 'src.rag.index'
assert 'src.web.app' not in sys.modules
assert 'src.web.rag_index' not in sys.modules
```

- [ ] 通过新旧索引模块逐一比较九个公开对象的identity；类identity在正常conftest隔离配置/DB环境下比较 `src.web.app.RAGSystem is src.rag.service.RAGSystem`，保留旧路径测试，不伪造`__module__`。
- [ ] 先运行新测试记录缺失canonical模块的RED，不能提前搬实现使RED缺失。已有实际RAG测试作为迁移前行为基线运行一次。

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -m pytest tests/test_rag_service_boundary.py tests/test_rag_index_manifest.py -q -p no:cacheprovider --tb=short -rs
```

## 任务2：原样归位并保持单一实现

- [ ] 用apply_patch把基线RAGSystem完整类块移到新service模块，其方法body不变。service的导入为datetime/timezone、logging、typing List/Dict/Any/Optional、pathlib Path、httpx、pandas、numpy、领域retrieval/index；原optional faiss try/except保留。logger使用本模块名。
- [ ] 原 `src/web/rag_index.py` 完整实现原样迁到领域index模块（除模块说明外不改算法），Web旧模块替换为以下显式兼容导出：

```python
"""Compatibility exports; canonical RAG index implementation lives in src.rag."""
from src.rag.index import (
    CURRENT_SCHEMA_VERSION,
    RAGIndexCompatibilityError,
    RAGIndexManifest,
    atomic_save_index_pair,
    file_sha256,
    immutable_index_snapshot,
    load_manifest,
    manifest_path,
    validate_manifest,
)

__all__ = [
    'CURRENT_SCHEMA_VERSION', 'RAGIndexCompatibilityError', 'RAGIndexManifest',
    'atomic_save_index_pair', 'file_sha256', 'immutable_index_snapshot',
    'load_manifest', 'manifest_path', 'validate_manifest',
]
```

- [ ] retrieval.py 的 `from src.web.rag_index import ...` 改为 `from src.rag.index import ...`，其他内容不变。app显式 `from src.rag.service import RAGSystem`，保留原实例化及注入，删除类专用依赖，保留仍使用的Dict/Any/Optional。
- [ ] manifest测试改到canonical service/index导入；hash/read注入点改为 `src.rag.service.file_sha256`，fsync/replace为 `src.rag.index.os.*`。不改变fixture数据、模拟攻击时点、结果、回滚或收尾断言。registration consistency仍通过旧路径导入以验证兼容。
- [ ] health检查用 `app_module.RAGSystem is importlib.import_module('src.rag.service').RAGSystem` 和新模块名替换旧归属断言，保留其他canonical与历史重复实现排除检查。
- [ ] 比较原类AST与新类AST、原index所有函数/类/常量AST与新实现，确认逻辑没变；app其他类/函数AST不变。fresh subprocess canonical导入不加载Web、无外部连接。输出与来源依然由实际原测试断言。

## 任务3：回归与发布证据

- [ ] 聚焦：新增边界、manifest、实际注册、正式Agent入口、聊天预算、平台健康与应用生命周期。
- [ ] 联合17路径：`tests/agent tests/test_model_request_lifecycle.py tests/test_design_model_switch.py tests/test_molecular_design_architecture.py tests/test_llm_runtime_config.py tests/test_user_llm_routes.py tests/test_agent_llm_wiring.py tests/test_task_runtime.py tests/test_phase2_phase3_routes.py tests/test_agent_anti_hallucination_fallbacks.py tests/test_agent_platform_health_check.py tests/test_agent_session.py tests/test_agent_session_entrypoints.py tests/test_agent_task_ownership.py tests/test_rag_index_manifest.py tests/test_web_app_lifecycle.py tests/test_openai_compatible_model.py`，并加入新增边界测试。
- [ ] 上述运行均使用 `python -B -m pytest -q -p no:cacheprovider --tb=short -rs`；所有real开关0，正常conftest隔离用户配置，外层TemporaryDirectory隔离AGENT_STATE_DB/MEDCHAT_TASK_DB_PATH；不得读取真实key。每条命令记录workdir/exit/通过跳过失败；不降级或安装新依赖。
- [ ] `AGENT_HARNESS_MODE=legacy` 下运行 `python -B scripts/run_agent_acceptance.py --mode contract --output scratch/t11a-contract.json`，报告仅scratch不提交；不可称作真实科学工具验收。
- [ ] 对改动Python内存compile与 `git diff --check`，核对写集、源树未动。若需要确认schema/算法/资源行为改变，停止纯提取并单独报告，不静默扩大范围。
- [ ] 先独立SPEC再QUALITY。父任务仅在双审和实际联合回归后精确提交；前置批次发布完成前不推送累计PR。最终重新对齐、最新CI7/7、无未解决审查、merge-tree匹配才合并，不启用生产模型或部署。

## 执行证据

### 执行范围与隔离

- 独立工作树：`D:/MedChat/molecular_chat_system_worktrees/rag-service-extraction-pr`；分支 `codex/rag-service-extraction-pr`。
- 开始时工作树干净，HEAD 保持 `3e6f16c06b7d81cb4c970912070c1607096411f0`。`eb34e79..HEAD` 仅有已批准 spec/plan 两份文档，源代码基线一致。
- 仅本计划声明的八个 Python 文件及本节发生变更。另有用户指定的、已被 gitignore 排除的运行产物 `scratch/t11a-contract.json`；没有新增 handoff，没有修改其他工作树。
- 未暂存、未提交、未 push、未创建 PR；未改算法、schema、资源/关闭策略、生产配置或模型；没有真实模型/API/科学工具验收。
- 原类完整迁入 service（仅清除被搬入类的空白行尾空格），索引完整迁入 index；旧入口是同一对象的显式别名，无包装类、重复实现或伪造归属。
- 所有验证使用指定 MedChat Python、`-B`、`-p no:cacheprovider` 和正常 conftest。外层进程清空非白名单环境，隔离用户配置、Agent/Task/session/target DB、cache、fragment 路径，并关闭 real/canary 开关。测试 cwd 位于外层 TemporaryDirectory，finally 调用 logging.shutdown，再退出/清理临时目录。
- 新边界测试另起白名单环境子进程、显式插入本 repo 路径，禁止 socket connect/connect_ex/create_connection；canonical 导入不构造服务、不读 CSV、不加载任何 Web 模块。旧 app identity 测试在临时配置/DB 下替换导入期模型和无关资产路由，不执行真实工具。

### TDD 与验证记录

所有命令从上述工作树发起；pytest 实际 cwd 为隔离临时目录。下表保留失败和中断，不把重跑结果覆盖到前一次记录。

| 阶段 | 范围/启动方式 | 结果 | exit |
|---|---|---|---|
| 迁移前基线 | 原 manifest 测试；临时隔离包装 + pytest.main | 53 passed，0 skipped，4 warnings；5.76s | 0 |
| RED | 新 boundary + 原 manifest；实现尚未迁移 | 11 failed、53 passed、0 skipped、4 warnings；6.83s。失败全部为 canonical service/index 缺失 | 1 |
| 首轮 GREEN | 下列 FOCUS 七路径 | 162 passed，0 skipped，4 warnings；11.70s | 0 |
| 最终代码快照 GREEN | FOCUS 七路径；仅已清除迁移类空白行的行尾空格 | 162 passed，0 skipped，4 warnings；15.41s | 0 |
| 首轮联合 | JOINT 十八路径；stdin 主模块、临时 cwd 尚未备齐离线 fixture | 4 failed、4177 passed、8 skipped、7 warnings；335.94s | 1 |
| 隔离包装诊断 | spawn 单测，原 stdin 启动 | 1 failed；45.92s；Windows spawn 尝试加载不存在的 `<stdin>` | 1 |
| 隔离包装诊断 | 同一 spawn 单测，仅改为 `-c` 启动 | 1 passed；1.29s | 0 |
| 中间联合重跑 | 仅修正 `-c`，尚未补齐离线 fixture | 手动中断（约22%），没有完整统计，不计作通过 | 1 |
| collect-only 诊断 | JOINT；未执行测试 | 4189 tests collected；11.34s | 0 |
| 最终聚焦/fixture 验证 | 正常 `-m pytest`；FOCUS 加三个 dataset fixture 测试 | 165 passed，0 skipped，4 warnings；15.94s | 0 |
| 最终联合 | 原17路径 + 新boundary，正常 `-m pytest`，完整保留所有测试 | **4181 passed、8 skipped、0 failed、7 warnings；279.02s** | **0** |
| 离线 contract | `AGENT_HARNESS_MODE=legacy`，`--mode contract` | **34/34 passed**；不是科学工具/真实模型验收 | **0** |
| 最终静态验证 | AST 对照、八个文件内存 compile、`git diff --check` | 全部通过 | 0 |

首轮联合失败原因及处理（仅运行包装变更）：

- `test_cross_process_race_has_exactly_one_winner`：Windows spawn 重新加载 stdin 主模块，报 `Invalid argument: <stdin>`，随后 queue 超时。以 `-c` 启动的单测为 1 passed；最终正常 `-m pytest` 子进程也通过。
- `test_real_agent_dataset_contains_all_docx_cases`、`test_golden_scientific_dataset_contains_12_traceable_cases`、`test_diverse_scientific_dataset_contains_20_traceable_cases`：三个测试使用相对路径，临时 cwd 下缺少 tracked 离线评测 JSONL。最终包装仅将原文件复制到临时目录，并逐份校验 SHA-256 一致；没有编辑数据或测试，没有启用 real 模式。
- 一次临时静态辅助检查额外检查了旧 app 全文件行尾空格，因基线已有空格而 exit 1；当时 AST 对照均已通过。未修改旧 app 空白，只把这项额外检查限于新增文件，重跑 exit 0。八个文件内存 compile 及最终 diff-check 均通过。

最终联合的八项既有跳过（未新增或修改 skip）：

1. `tests/agent/test_decision_chat_acceptance.py:149`：directory symlinks unavailable。
2. `tests/agent/test_harness_shadow.py:277`：performance test disabled。
3. `tests/test_llm_runtime_config.py:33`：Windows symlink 权限不可用（WinError 1314）。
4. `tests/test_agent_session.py:113`：POSIX directory permission semantics。
5. `tests/test_agent_session.py:121`：POSIX directory permission semantics。
6. `tests/test_agent_task_ownership.py:177`：requires two independent task stores。
7. `tests/test_agent_task_ownership.py:201`：requires a configured runtime。
8. `tests/test_agent_task_ownership.py:218`：requires two independent task stores。

警告为 FastAPI on_event 弃用（4项）和 SWIG 类型归属弃用（联合额外3项），未通过修改资源策略或删除断言处理。

### 实际验证命令与包装

下面是最终联合使用的完整隔离包装；它没有保存为新脚本。仅复制上述三份仓库内已跟踪的离线 fixture，临时目录随进程完成清理。保持正常 conftest，不加 `--noconftest`。

```powershell
$python = 'C:/Users/xkx52/.conda/envs/MedChat/python.exe'
$runner = @'
import logging, os, sys, tempfile
from pathlib import Path
repo = Path(r"D:/MedChat/molecular_chat_system_worktrees/rag-service-extraction-pr")
keep = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "COMSPEC") if key in os.environ}
os.environ.clear()
os.environ.update(keep)
with tempfile.TemporaryDirectory(prefix="medchat-t11a-") as temporary:
    root = Path(temporary)
    os.environ.update({
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(repo), "AGENT_HARNESS_MODE": "legacy",
        "MOLECULAR_CHAT_CONFIG": str(root / "missing.yaml"),
        "MEDCHAT_ENV_FILE": str(root / "not-loaded.env"),
        "MEDCHAT_USER_CONFIG_DIR": str(root / "user-config"),
        "MEDCHAT_LLM_LOCK_DIR": str(root / "locks"),
        "MEDCHAT_AGENT_SESSION_DB": str(root / "sessions.sqlite"),
        "AGENT_STATE_DB": str(root / "agent.sqlite"),
        "MEDCHAT_TASK_DB_PATH": str(root / "tasks.sqlite"),
        "TARGET_DB_PATH": str(root / "targets.sqlite"),
        "TARGET_CACHE_DIR": str(root / "target-cache"),
        "MEDCHAT_FRAGMENT_DB_PATH": str(root),
        "MEDCHAT_TASK_BACKEND": "local", "MEDCHAT_TEMPORAL_CANARY_PERCENT": "0",
        "AGENT_LANGGRAPH_CANARY_PERCENT": "0",
        "MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE": "0",
        "MEDCHAT_RUN_OPENSANDBOX_ACCEPTANCE": "0",
        "MEDCHAT_RUN_OPENSANDBOX_STABILITY_SOAK": "0",
        "RUN_REAL_TARGET_SEARCH": "0",
    })
    os.chdir(root)
    sys.path.insert(0, str(repo))
    try:
        import hashlib, shutil, subprocess
        fixtures = root / "data/agent_evals"
        fixtures.mkdir(parents=True)
        for name in ("real_agent_cases.jsonl", "golden_scientific_cases.jsonl", "diverse_scientific_cases.jsonl"):
            source = repo / "data/agent_evals" / name
            destination = fixtures / name
            shutil.copy2(source, destination)
            assert hashlib.sha256(source.read_bytes()).digest() == hashlib.sha256(destination.read_bytes()).digest()
        result = subprocess.call([sys.executable, "-B", "-m", "pytest"] +
            [str(repo / path) for path in sys.argv[1:]] +
            ["-q", "-p", "no:cacheprovider", "--tb=short", "-rs"])
    finally:
        logging.shutdown()
        os.chdir(repo)
    print("T11A_PYTEST_EXIT=" + str(result))
    sys.exit(result)
'@
$focus = @(
    'tests/test_rag_service_boundary.py',
    'tests/test_rag_index_manifest.py',
    'tests/agent/test_registration_consistency.py',
    'tests/agent/test_app_supervisor_entrypoint.py',
    'tests/agent/test_chat_input_budget.py',
    'tests/test_agent_platform_health_check.py',
    'tests/test_web_app_lifecycle.py'
)

$joint = @(
    'tests/agent',
    'tests/test_model_request_lifecycle.py',
    'tests/test_design_model_switch.py',
    'tests/test_molecular_design_architecture.py',
    'tests/test_llm_runtime_config.py',
    'tests/test_user_llm_routes.py',
    'tests/test_agent_llm_wiring.py',
    'tests/test_task_runtime.py',
    'tests/test_phase2_phase3_routes.py',
    'tests/test_agent_anti_hallucination_fallbacks.py',
    'tests/test_agent_platform_health_check.py',
    'tests/test_agent_session.py',
    'tests/test_agent_session_entrypoints.py',
    'tests/test_agent_task_ownership.py',
    'tests/test_rag_index_manifest.py',
    'tests/test_web_app_lifecycle.py',
    'tests/test_openai_compatible_model.py',
    'tests/test_rag_service_boundary.py'
)

$runner | & $python -B -c "import sys; exec(sys.stdin.read())" @joint
```

最终聚焦命令使用相同 runner，参数为下列三个 node id 后接 `@focus`（165 passed）：

```powershell
$fixtureChecks = @(
    'tests/agent/test_evaluation_runner.py::test_real_agent_dataset_contains_all_docx_cases',
    'tests/agent/test_evaluation_runner.py::test_golden_scientific_dataset_contains_12_traceable_cases',
    'tests/agent/test_evaluation_runner.py::test_diverse_scientific_dataset_contains_20_traceable_cases'
)
$runner | & $python -B -c "import sys; exec(sys.stdin.read())" @fixtureChecks @focus
```

迁移前基线/RED/早期 GREEN 的环境白名单、TemporaryDirectory、conftest、参数与 finally 收尾相同；没有 fixture 复制，其 try 块调用：

```python
import pytest
result = pytest.main([str(repo / path) for path in sys.argv[1:]] +
                     ["-q", "-p", "no:cacheprovider", "--tb=short", "-rs"])
```

最初以 `$runner | & $python -B - <paths>` 启动。基线路径仅 `tests/test_rag_index_manifest.py`；RED 路径为 `tests/test_rag_service_boundary.py tests/test_rag_index_manifest.py`；早期 GREEN 为 FOCUS。中间 `-c` 重跑仍使用 pytest.main；最终改为上面正常 `-m pytest` 子进程。表中记录了全部完整回归与中断，不将中断记为成功。

contract 在同一临时隔离环境中执行 `scripts/run_agent_acceptance.py --mode contract --output scratch/t11a-contract.json`；因为 cwd 是临时目录，实际 argv 的脚本与 output 均用 repo 绝对路径。没有 pytest 调用，try 块替换为：

```python
import runpy, socket
def blocked(*args, **kwargs):
    raise AssertionError("Offline contract must not connect to services")
socket.socket.connect = blocked
socket.socket.connect_ex = blocked
socket.create_connection = blocked
sys.argv = [str(repo / "scripts/run_agent_acceptance.py"), "--mode", "contract",
            "--output", str(repo / "scratch/t11a-contract.json")]
try:
    runpy.run_path(sys.argv[0], run_name="__main__")
except SystemExit as exc:
    result = exc.code
```

该报告包含 34 个离线契约案例，虽然部分案例 ID 为 REAL-*，本次并未运行 real 模式。报告 SHA-256：`7ca5c171da65c48aaad2bebf5e4e275259c0c78843e510bb0c5a7e18e14fd3c8`；产物被忽略，未暂存。

### AST、内存编译与同一快照

用指定 Python `-B -` 从 stdin 运行只读静态脚本，以 `git show HEAD:<path>` 为旧源码，`ast.dump(..., include_attributes=False)` 为比较值，逐项 assert：

- RAGSystem 完整类 AST 相同，共10个方法（含构造/同步/异步方法）。
- 原 Web index 与新 index 的完整模块 AST 相同，包含全部 imports/classes/functions/constants。
- app 其他6个顶层类/函数 AST 相同；额外核对排除迁移类、import、原 optional FAISS try 块后的顶层语句全部相同。
- retrieval 全文件仅一处 import 路径变化。
- manifest 测试全文件仅批准的两个 import 与 service hash/index os patch 路径替换；攻击时点、失败/来源/回滚/cleanup 断言完整保留。
- 八个变更 Python 文件 `compile(content, path, "exec")` 全部通过；没有写入 pyc。新文件行尾检查通过，`git diff --check` exit 0。

最终聚焦、最终联合、contract 和最终静态核验均对应以下代码/测试快照；其后仅更新本执行记录。摘要不包含本记录自身，避免自引用。

```text
src/rag/index.py 951e958025d3e1f7d640d6709c79b9f9d60334abfa045aaa09bb308aedf39983
src/rag/retrieval.py f2c3dd327131a21214c26ab897d40f2dcc577c33abdd09eb59bae08a7c432f64
src/rag/service.py ec0302dc7cc92d8705393c41edc6d6f3678003603d17254637b5504e6793adbc
src/web/app.py e14fa74a6e97366908db695efb8ed751e909e0eec617f8f9f2b45fc83edaf4dc
src/web/rag_index.py 37ac848358e55b950e7428ea2ed1eb8a1a66d5246a5ddf17f91dabd0bbb5ec4a
tests/test_agent_platform_health_check.py 1ed3b97f1fde0d176926ff8df8b2adb70856599099f8a8c08904cbabe6852184
tests/test_rag_index_manifest.py f70d718556fc2710a5ec2bab6498be47bff6bd201a7294b68c75df80879dca60
tests/test_rag_service_boundary.py ad3176a96d63b3a1459721f74753125861e0dabacb51a8edefa5e8054515b3b8
```

算法：按相对路径排序，逐行 `path + " " + sha256(raw_file_bytes)`，每行末尾包括最后一行均为 LF，对合成 UTF-8 字节再 SHA-256。

**CODE_SNAPSHOT_SHA256：`c9be016fac187e57ad020641b17b411f1adde49480ed0bebf6281248d750c26f`。**

### 停止点

实现及本地验证完成。严格保持九文件写集；停止写入，等待父任务独立 **SPEC → QUALITY**，本记录不代表独立审查已通过。无新 commit/PR；HEAD 未移动，暂存区为空。未进行部署、生产模型启用、真实科学验收或未授权的范围外修复。

### 父任务独立审查追加

上节为实现者交接时点；随后同一代码/测试快照已通过独立 SPEC → QUALITY，无阻断意见。

- SPEC：独立核对全部 AST、旧导入 identity、领域导入无 Web/CSV/HTTP 初始化、真实 manifest 测试注入点；另跑166 passed、0 skipped、4个既有弃用警告，exit0。包含原七组聚焦、三个离线 fixture 检查和 Windows spawn 竞争测试。首次审查包装误拦 Windows 内部 `_fallback_socketpair`，产生42 failed/87 passed/37 errors；仅修正审查包装后完整重跑通过，未修改仓库或测试。
- QUALITY：独立核对相同快照，59 passed、5 deselected，9.17秒、exit0。五个全局 app 组装用例未在此次窄回归重跑，已由实现者及SPEC完整覆盖；不把 deselected 计为 passed。确认新测试无 lifespan/后台任务启动，隔离临时目录正常清理；8文件内存编译与diff-check通过。
- 两次审查均只读；联合4181通过/8跳过及离线contract34/34是实现者的已核验证据，审查者未声称各自重跑全量或真实科学工具。
- 仅计划追加此节，代码快照仍为上列 `c9be016f...`。父任务接下来精确提交九路径；前置批次尚须逐项发布，不能将累计分支直接作为单个PR推送或标记任务书全部完成。

### 最新依赖组合验证（父任务，2026-09-24）

前置模板兼容、匿名会话、T01共享RAG、T06-B静态备份清理及T03预算修复已分批合并。T05仍独立Draft PR44；本地已审的T05静态测试适配通过SPEC/QUALITY，只移除重复model.close并增加实际gate.closed断言。其后顺序传播到T06A/T06C/T11A，原三批3/3/10路径binary主题补丁逐字节不变；独立增量审查APPROVED，未再修改生产代码。

本次受测HEAD为`00a00cd0be3c44f5f5b628ac3bcdacda00b9a1dd`，完整tree为`f5193ba4bf91b7a12c2aae4a0e81a4a7237e0213`。在上述JOINT十八路径基础上加入`tests/test_main_routes_template_compat.py`与`tests/test_static_placeholder_cleanup.py`，使用完全相同的正常`-m pytest`隔离包装和参数，结果为 **4231 passed、8 skipped、7 warnings，289.04秒，exit0**。这是Agent/Web/RAG/生命周期组合，不是全仓或真实模型科研验收；八项跳过及警告原因仍与上文相同，没有新增skip。

同一临时环境和显式socket联网阻断下，重跑`run_agent_acceptance.py --mode contract`，输出到忽略路径`scratch/t11a-contract-static-combination.json`，**34/34 passed、pass_rate=1.0、exit0**。报告SHA256为`0977d3744d5a48174c33eeb086f0bea8997d691f063f01f4a5d77bf6e54d3d9b`；未提交产物。RDKit日志中的无效SMILES属于预期拒绝用例，不是调用真实外部模型。

`src`与`scripts`全部302个跟踪Python文件内存编译通过，未写pyc；diff检查通过。独立审查者核对了增量blob/mode、原主题补丁、关闭责任、无lifespan/索引初始化边界，没有声称自行重跑4231项。此后只追加本节执行记录。仍须依次完成T05/T06A/T06C发布，才能给本批创建精确PR；没有部署或更改生产资产。
