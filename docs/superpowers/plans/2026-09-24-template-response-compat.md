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

- [ ] 新测试通过真实 `FastAPI`、`Jinja2Templates(directory=tmp_path)` 和 `register_main_routes` 构造页面。以下八项参数化，并另逐项验证 templates=None 时原 JSON 不变：

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

- [ ] 每个临时 HTML 模板使用自身文件名和以下内容；发带 `q=<script>alert(1)</script>` 的真实请求，断言状态200、文件名和请求路径正确、url_for得到首页地址、script被转义而非执行文本。

```jinja2
{{ request.url.path }} | {{ url_for('home') }} | {{ request.query_params.get('q', '') }}
```

- [ ] 将计数函数注入 Jinja globals，模板调用该函数且函数抛出 `TypeError('template-render-failed')`。实际 TestClient 请求必须透传此错误，计数恰好1，不能接受参数错误冒充渲染错误。
- [ ] 在旧版真实库下跑新增测试，记录 `request` 参数不兼容的失败，不能以导入错误作为 RED。旧库已在 `scratch/template-legacy-deps`，通过进程内 sys.path 前置，不改 Conda：

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -c "import sys; sys.path.insert(0, 'scratch/template-legacy-deps'); import fastapi,starlette,httpx,pytest; print(fastapi.__version__,starlette.__version__,httpx.__version__); raise SystemExit(pytest.main(['tests/test_main_routes_template_compat.py','-q','-p','no:cacheprovider','--tb=short','-rs']))"
```

## 任务 2：最小实现与边界测试

- [ ] 增加 `from inspect import signature`；在 `register_main_routes` 的路由定义前加入以下逻辑（函数命名可沿模块风格调整，语义不变）：

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

- [ ] 八处 `if templates is not None` 保持不变，将内部返回替换为 `return render_template(request, '<原模板名>')`。静态知识页面原样保留。
- [ ] 增加旧显式签名 `(name, context)`、新显式签名 `(request, name, context)`、过渡 `(*args, **kwargs)` 的边界测试，用记录调用的对象返回 Response，通过实际路由请求断言收到正确 Request/模板/上下文。这些边界测试补充而非替代真实 Jinja 测试。
- [ ] patch 本模块的 signature 用计数包装原函数，注册一次后请求两次，断言只检查一次；templates=None 时不调用 signature。不要让测试依赖私有 helper 名字。
- [ ] 新版本地库与旧版隔离库各运行新增测试，两组均需通过。旧/新各重复实际渲染异常测试，确认没有重试。

## 任务 3：审查与发布

- [ ] 用正常 pytest 入口运行新增测试及 `tests/test_user_llm_routes.py`、`tests/test_agent_session_entrypoints.py`、`tests/test_web_app_lifecycle.py`；设置所有 real 开关为0，用户配置/Agent与任务DB使用临时目录。若新旧库的既有测试有基线不兼容，如实定位，不静默改写无关测试。
- [ ] 内存 compile 改动的 Python，不写字节码；`git diff --check`。核对除指定路由调用外所有路由 URL/名字/fallback/静态页面不变，无新外部依赖。
- [ ] 记录真实 RED/GREEN、版本、退出码、skip/失败原因于本计划；不得把此测试称为真实模型或生产验收。
- [ ] 独立 SPEC 后 QUALITY。发现问题交回同一实现者修复并复审。
- [ ] 父任务精确暂存批准路径，提交、创建 draft PR、附加任务；仅最新 head 的7项CI全成功、无未解决审查且merge-tree一致时合并。之后PR37整合本修复再验证，不盲目重跑旧失败记录。

## 执行证据

尚未实施；本节由真实命令结果更新。PR37原始CI run `35876507686` 的模板失败继续保留。
