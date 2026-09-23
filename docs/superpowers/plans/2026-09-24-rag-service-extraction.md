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

尚未实施；此处后续记录真实结果，不把设计/静态核对当作通过。
