# 主页面模板接口兼容修复设计

## 已复现问题

PR #37 首次 CI run `35876507686` 的 root job `107233690262`：两条 WebSocket 配置测试请求首页时失败，旧版 `Jinja2Templates.TemplateResponse` 不接受 `request=`。根依赖固定 FastAPI 0.104.1，对应 Starlette 0.27；开发机 FastAPI 0.135.3 / Starlette 1.0.0 使用新签名。它是已有页面路由的真实兼容缺口，不能通过 mock 首页绕过。

## 最小方案（待用户确认）

- 不升级依赖，不修改会话、模型、模板或资源路径。
- 仅在 `src/web/routes/main_routes.py` 注册主页面路由时检查绑定 `TemplateResponse` 的签名一次。
- 若签名显式包含 `request`，用新式 `request=request, name=name, context={}`；否则用旧式 `name=name, context={"request": request}`。中间版本的 `*args/**kwargs` 仍支持旧式 context 调用。
- 八个现有 Jinja 页面使用同一个局部适配函数，URL、模板名及无模板时的 JSON 返回保持不变。静态知识页面不改。
- 不捕获渲染 TypeError 后重试；模板自身异常保留原样并且仅调用一次，避免掩盖真实错误。

备选：只恢复旧语法会使 Starlette 1.0.0 缺少 request 参数；升级整套依赖扩大范围。本方案同时保留当前两类部署。

## 测试与发布边界

先写真实 FastAPI/Jinja 页面测试，在仓库固定旧版库的隔离导入路径下取得 RED，再实现适配。用新版和旧版实际库重复测试：八个页面、无模板 API fallback、request/url_for 可用性、HTML 转义、模板渲染错误不重试。增加显式旧/新/过渡签名边界测试，不能仅依赖模板 mock。

旧库只安装到本工作树被忽略的 scratch 目录，不降级用户 Conda 环境，不读取真实密钥或运行模型。测试用临时模板和独立 FastAPI 路由，不导入全局 MedChat 应用。受影响的真实首页/WebSocket 和静态资产集成在依赖批次合入后另跑。

独立 SPEC/QUALITY、精确提交、draft PR、7 项 CI 全过后才能合并；不直接修改 main。保留原始 CI 失败和依赖版本记录，不将单元测试通过写成整体部署验收。
