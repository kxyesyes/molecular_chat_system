# 主页面 TemplateResponse 兼容实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox syntax for tracking.

**Goal:** 修复旧版真实首页渲染失败，同时保留新版支持、八个页面协议与模板异常。

**Architecture:** 注册路由时仅检查绑定方法的公开签名一次，以是否含显式 request 参数选择调用形态。所有页面共享一个局部调用函数，不捕获渲染错误重试，不修改依赖或服务初始化。

**Tech Stack:** Python 3.10、FastAPI、Starlette、Jinja2、HTTPX TestClient、pytest。

## 写入范围与边界

- 修改 `src/web/routes/main_routes.py`：签名判断和八处模板调用。
- 新增 `tests/test_main_routes_template_compat.py`：真实页面与签名边界回归。
- 更新本计划的执行证据；设计位于同目录的 specs 对应文件。
- 不更改其他源文件、依赖、CI、配置、真实资产或旧测试。测试不导入全局 `src.web.app`，不创建真实客户端/模型；临时模板和 TestClient 均由 fixture/context manager 收尾。

## 任务 1：真实接口 RED

- [x] 新测试通过真实 `FastAPI`、`Jinja2Templates(directory=tmp_path)` 和 `register_main_routes` 构造页面。以下八项参数化，并另逐项验证 templates=None 时原 JSON 不变：

```python
PAGES = [
    ("/", "index.html", "Molecular Chat System API"),
    ("/molecular-docking", "molecular_docking.html", "Molecular Docking System"),
    ("/reverse-docking", "reverse_target.html", "Reverse Target Prediction System"),
    ("/reverse-target", "reverse_target.html", "Reverse Target Prediction System"),
    ("/activity-prediction", "activity_prediction.html", "Activity Prediction System"),
    ("/kermt-admet", "kermt_admet.html", "KERMT ADMET Prediction System"),
    ("/molecular-design", "molecular_design.html", "Molecular Design System"),
    ("/target-search", "target_search.html", "Target Search Demo"),
]
```

- [x] 每个临时 HTML 模板使用自身文件名和以下内容；发带 `q=<script>alert(1)</script>` 的真实请求，断言状态200、文件名和请求路径正确、url_for得到首页地址、script被转义而非执行文本。

```jinja2
{{ request.url.path }} | {{ url_for('home') }} | {{ request.query_params.get('q', '') }}
```

- [x] 将计数函数注入 Jinja globals，模板调用该函数且函数抛出 `TypeError('template-render-failed')`。实际 TestClient 请求必须透传此错误，计数恰好1，不能接受参数错误冒充渲染错误。
- [x] 在旧版真实库下跑新增测试，记录 `request` 参数不兼容的失败，不能以导入错误作为 RED。旧库已在 `scratch/template-legacy-deps`，通过进程内 sys.path 前置，不改 Conda：

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -c "import sys; sys.path.insert(0, 'scratch/template-legacy-deps'); import fastapi,starlette,httpx,pytest; print(fastapi.__version__,starlette.__version__,httpx.__version__); raise SystemExit(pytest.main(['tests/test_main_routes_template_compat.py','-q','-p','no:cacheprovider','--tb=short','-rs']))"
```

## 任务 2：最小实现与边界测试

- [x] 增加 `from inspect import signature`；在 `register_main_routes` 的路由定义前加入以下逻辑（函数命名可沿模块风格调整，语义不变）：

```python
explicit_request = (
    templates is not None
    and 'request' in signature(templates.TemplateResponse).parameters
)

def render_template(request: Request, name: str):
    if explicit_request:
        return templates.TemplateResponse(request=request, name=name, context={})
    return templates.TemplateResponse(name=name, context={'request': request})
```

- [x] 八处 `if templates is not None` 保持不变，将内部返回替换为 `return render_template(request, '<原模板名>')`。静态知识页面原样保留。
- [x] 增加旧显式签名 `(name, context)`、新显式签名 `(request, name, context)`、过渡 `(*args, **kwargs)` 的边界测试，用记录调用的对象返回 Response，通过实际路由请求断言收到正确 Request/模板/上下文。这些边界测试补充而非替代真实 Jinja 测试。
- [x] patch 本模块的 signature 用计数包装原函数，注册一次后请求两次，断言只检查一次；templates=None 时不调用 signature。不要让测试依赖私有 helper 名字。
- [x] 新版本地库与旧版隔离库各运行新增测试，两组均需通过。旧/新各重复实际渲染异常测试，确认没有重试。

## 任务 3：审查与发布

- [x] 本批局部回归：用正常 pytest 入口运行新增真实模板测试及 `tests/test_user_llm_routes.py`、`tests/test_web_app_lifecycle.py`；设置所有 real 开关为0，用户配置/Agent与任务DB使用临时目录。新旧库均 53 passed、exit 0；不静默改写无关测试。
- [ ] 跨批组合回归：父任务已确认 `tests/test_agent_session_entrypoints.py` 属于尚未合并的 PR37，本批 main 基线没有该文件；待与 PR37 组合后运行，不属于本批漏实现，不新建或替换。
- [x] 内存 compile 改动的 Python，不写字节码；`git diff --check`。核对除指定路由调用外所有路由 URL/名字/fallback/静态页面不变，无新外部依赖。
- [x] 记录真实 RED/GREEN、版本、退出码、skip/失败原因于本计划；不得把此测试称为真实模型或生产验收。
- [x] 独立 SPEC 后 QUALITY。发现问题交回同一实现者修复并复审。
- [ ] 父任务精确暂存批准路径，提交、创建 draft PR、附加任务；仅最新 head 的7项CI全成功、无未解决审查且merge-tree一致时合并。之后PR37整合本修复再验证，不盲目重跑旧失败记录。

## 执行证据

### 2026-09-24 实施结果（未提交，待父任务审查）

实施前工作树干净，分支 `codex/template-response-compat`，HEAD `638d2fb`，包含已对齐 main `fa04e6e` 的设计/计划。已读取 AGENTS、项目规范、设计和本计划；先新增测试取得 RED，后修改路由。只改本计划、`src/web/routes/main_routes.py` 和新增 `tests/test_main_routes_template_compat.py`。没有暂存、commit、push 或 PR；原始 CI run `35876507686` 的失败记录不变。

测试共 43 项：真实页面 8、fallback 8、真实模板异常 1、三类签名各覆盖八页共 24、签名检查次数/None 2。异常测试检查消息、异常对象身份与计数 1，不接受参数错误。无 skip、无断言删除，不导入全局 app；相关既有测试自身的 app 导入仍保留正常行为。

| 执行 | 结果 | 进程退出码 |
|---|---|---|
| 旧库 RED，修改路由之前 | 27 failed, 16 passed, 2 warnings | 1 |
| 新库新增测试 GREEN | 43 passed, 1 warning | 0 |
| 旧库新增测试 GREEN | 43 passed, 2 warnings | 0 |
| 新库单独复跑模板异常 | 1 passed, 1 warning | 0 |
| 旧库单独复跑模板异常 | 1 passed, 2 warnings | 0 |
| 新库新增 + user_llm_routes + web_app_lifecycle（最终） | 53 passed, 8 warnings | 0 |
| 旧库同上（最终） | 53 passed, 10 warnings | 0 |
| 指定 session_entrypoints 文件 | file or directory not found；无测试执行 | 4 |
| 两个改动 Python 内存 compile | 通过，无字节码输出 | 0 |
| 路由 AST 保持性核对 | 九个 URL/名称/参数、八个 fallback/guard、静态知识路由不变 | 0 |
| `git diff --check` | 无输出 | 0 |

旧库为 FastAPI 0.104.1 / Starlette 0.27.0 / HTTPX 0.25.2 / AnyIO 3.7.1；新库为 0.135.3 / 1.0.0 / 0.28.1 / 3.7.1。只以前置 sys.path 使用已有隔离库，没有安装或降级依赖。

RED 的八个真实页面均失败于 `TypeError: Jinja2Templates.TemplateResponse() got an unexpected keyword argument 'request'`，不是导入失败。另有渲染异常测试 1 项（实际参数错误不匹配指定错误）、旧显式/过渡签名 16 项、尚未加入 signature 的计数测试 2 项失败。16 项通过的是 fallback 与新显式签名。

### 执行命令

以下命令在指定工作树执行，使用正常 pytest/conftest，均 `-B` 和 `-p no:cacheprovider`。未启用真实 API、模型推理或生产验收；临时模板由 tmp_path 管理，TestClient 使用上下文管理器。相关回归额外将 cwd 放入系统临时目录，防止现有日志和 docking 初始化在工作树写文件；用户配置与环境文件由正常 conftest 隔离，Agent/任务/靶点 DB 均指向临时目录。

旧库 RED 和旧库新增测试 GREEN 使用同一命令；新库 GREEN 只删除 `sys.path.insert(0,'scratch/template-legacy-deps');`。单独异常复跑仅把 pytest 路径替换为 `tests/test_main_routes_template_compat.py::test_template_render_type_error_propagates_without_retry`：

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -c "import os,sys,tempfile; from pathlib import Path; sys.path.insert(0,'scratch/template-legacy-deps'); import fastapi,starlette,httpx,pytest; from importlib.metadata import version; print('VERSIONS',fastapi.__version__,starlette.__version__,httpx.__version__,version('anyio')); flags=('MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE','MEDCHAT_RUN_OPENSANDBOX_ACCEPTANCE','RUN_REAL_TARGET_SEARCH'); os.environ.update(dict.fromkeys(flags,'0'));
with tempfile.TemporaryDirectory(prefix='medchat-template-compat-') as root:
 os.environ.update({key:str(Path(root)/name) for key,name in {'AGENT_STATE_DB':'agent.sqlite','MEDCHAT_TASK_DB_PATH':'tasks.sqlite','TARGET_DB_PATH':'targets.sqlite','TARGET_CACHE_DIR':'cache'}.items()}); code=pytest.main(['tests/test_main_routes_template_compat.py','-q','-p','no:cacheprovider','--tb=short','-rs'])
raise SystemExit(code)"
```

最终旧库相关回归命令如下；新库只删除 `sys.path.insert(0,str(repo/'scratch/template-legacy-deps'));`：

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -c "import os,sys,tempfile,logging; from pathlib import Path; repo=Path.cwd(); sys.path.insert(0,str(repo)); sys.path.insert(0,str(repo/'scratch/template-legacy-deps')); import fastapi,starlette,httpx,pytest; from importlib.metadata import version; print('VERSIONS',fastapi.__version__,starlette.__version__,httpx.__version__,version('anyio')); flags=('MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE','MEDCHAT_RUN_OPENSANDBOX_ACCEPTANCE','RUN_REAL_TARGET_SEARCH'); os.environ.update(dict.fromkeys(flags,'0')); os.environ.update({'MEDCHAT_TASK_BACKEND':'local','MEDCHAT_TEMPORAL_CANARY_PERCENT':'0','RXN_API_KEY':''});
with tempfile.TemporaryDirectory(prefix='medchat-template-regression-') as root:
 os.environ.update({key:str(Path(root)/name) for key,name in {'AGENT_STATE_DB':'agent.sqlite','MEDCHAT_TASK_DB_PATH':'tasks.sqlite','TARGET_DB_PATH':'targets.sqlite','TARGET_CACHE_DIR':'cache','MOLECULAR_CHAT_CONFIG':'missing.yaml','MEDCHAT_TASK_STAGING_ROOT':'staging'}.items()}); os.chdir(root)
 try: code=pytest.main([str(repo/path) for path in ['tests/test_main_routes_template_compat.py','tests/test_user_llm_routes.py','tests/test_web_app_lifecycle.py']]+['-q','-p','no:cacheprovider','--tb=short','-rs'])
 finally: logging.shutdown(); os.chdir(repo)
raise SystemExit(code)"
```

缺失文件检查沿用新增测试的新库命令，将路径替换成 `tests/test_agent_session_entrypoints.py`；最后用 `print('PYTEST_EXIT',int(code)); raise SystemExit(code)`，PowerShell 跟随 `exit $LASTEXITCODE`，明确得到 pytest/进程退出码 4。`rg --files tests` 与 `git ls-tree -r --name-only HEAD tests` 均未找到该文件，没有擅自替换或新增它。父任务随后确认该文件属于尚未合并的 PR37，本批 main 基线没有该文件：保留此次检查证据，归类为与 PR37 跨批组合后运行，而非本批漏实现。本批局部回归由新增真实模板测试、`tests/test_user_llm_routes.py` 和 `tests/test_web_app_lifecycle.py` 构成，已完成；跨批组合回归另列待办。

内存编译与差异检查：

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -c "from pathlib import Path; paths=('src/web/routes/main_routes.py','tests/test_main_routes_template_compat.py'); [compile(Path(p).read_bytes(),p,'exec') for p in paths]; print('Memory compile OK:', ', '.join(paths))"
git diff --check
git diff --cached --name-only
git status --short
```

另通过 `ast.parse(git show HEAD:src/web/routes/main_routes.py)` 与当前源码逐项比较全部 async 路由的名称、参数、decorator，以及页面的最终 fallback 和 templates guard；静态知识路由整棵 AST 相等。人工 diff 核对只新增签名检查/局部适配函数并替换八处模板调用，无 TypeError 捕获或重试，无依赖、模板、配置改动。

### 警告、收尾与交接边界

- 首次新旧相关回归都已 53 passed，但临时目录退出时现有 `src/utils/logger.py` 的 FileHandler 仍持有日志，Windows 清理报 WinError 32，命令 exit 1。仅在运行命令 finally 中补 `logging.shutdown()` 后重跑，两套库最终 exit 0；没有修改应用日志代码、测试断言或生产配置。
- 首次失败遗留的两个本次系统临时目录，定向清理请求被工具策略拒绝，未绕过；可能残留日志，不在工作树中。最终运行的临时目录正常收尾。
  - 新库首次运行：`C:\Users\xkx52\AppData\Local\Temp\medchat-template-regression-m7gkfaa5`
  - 旧库首次运行：`C:\Users\xkx52\AppData\Local\Temp\medchat-template-regression-vvwsr498`
  - 以上精确路径来自本轮失败 traceback，失败文件均为各目录下的 `logs/chat_20260924.log`；本次补充记录没有枚举临时目录或重新清理，不声称当前仍存在。父任务仅核对必要 cleanup，不改生产日志。
- 警告未屏蔽：预导入库引起 AnyIO assert-rewrite warning、既有 SWIG/on_event deprecation；最终旧库另有 pytest 共享临时目录垃圾清理 warning（WinError 145），测试及本次运行命令仍 exit 0。
- 本轮未运行 PR37 所属的 session_entrypoints（待跨批组合后运行，不属于本批漏实现），也未运行全量测试、真实模型/科学工具、CI 或部署验收，不宣称这些已通过。
- 独立 SPEC 后 QUALITY 由父任务负责，仍未勾选。实施者到此停写；无暂存、提交、push、PR 或合并，父任务的发布动作不得视为本轮已授权执行。

### 父任务后续独立审查（以上为实现交接时点）

- SPEC APPROVED：独立新旧库各53 passed、exit0；核对真实Jinja覆盖和路由AST，未改文件。
- QUALITY APPROVED：独立新库53 passed/8 warnings、旧库53 passed/9 warnings，均exit0；额外内存探针确认共享Jinja跨应用/请求隔离、缺失模板/语法错误单次传播和无全局app导入。本轮审查临时目录均正常清理；不抹去历史runner清理失败。
- 实现代码SHA256 `3FB93BECF57390707ADEDFE32D9A6A332E4DDF7475C8ABA751B9C0D92387308E`、新增测试 `8C5695CDF72A8AFD9027DBC9496559C81B1285A588E74F47BA9B6FC65693D2E3` 在双审前后相同。四个本任务文件的凭据模式扫描未发现匹配。
- 当前仅达到本地双审通过；父任务依总目标发布独立draft PR，仍需最新CI7/7和无未解决审查才可合并。PR37跨批测试另行完成，不把本地小矩阵称作全仓或生产验收。
