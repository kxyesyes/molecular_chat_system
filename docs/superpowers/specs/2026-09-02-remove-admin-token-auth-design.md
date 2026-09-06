# 移除管理员令牌鉴权设计

## 目标

彻底移除 MedChat 当前的管理员令牌功能。页面不再弹出“请输入 MedChat 管理员令牌”，浏览器不再保存或发送管理员令牌，项目内原先受 `require_admin` 保护的管理接口不再要求该令牌。

## 范围

- 删除后端 `MEDCHAT_ADMIN_TOKEN`、`X-MedChat-Admin-Token`、Bearer token 校验及相关依赖注入。
- 从模型配置、任务、工作流、系统信息、对接历史和活性模型管理路由移除管理员令牌依赖。
- 删除前端 `admin_fetch.js` 的令牌存储、prompt 和 Header 注入逻辑。
- 将现有调用方改为普通同源 `fetch`，并从模板移除 `admin_fetch.js`。
- 删除或改写仅验证管理员令牌的测试，保留接口功能、敏感信息脱敏和 XSS 回归测试。
- 更新 `.env.example` 及项目文档，删除管理员令牌配置说明。

## 不变边界

- LLM API Key 仍只写本机 `.env`，不从接口返回明文，也不进入 Git。
- 现有 API 输入校验、路径约束、任务隔离、结果脱敏和科学真实性校验保持不变。
- 本次不新增登录、Cookie、IP 白名单或替代鉴权机制。
- 不修改与管理员令牌无关的业务逻辑。

## 风险确认

移除后，任何能够访问 MedChat HTTP 服务的客户端都可以调用原管理接口，包括修改模型配置、删除对接历史、切换或删除活性模型等。因此远程部署必须由 nginx、VPN、防火墙或其他外层访问控制承担安全责任。本风险由用户明确接受。

## 验收标准

- 页面源码及运行时不再调用 `window.prompt()`，截图中的令牌弹窗无法再出现。
- 仓库中不再存在 `MEDCHAT_ADMIN_TOKEN`、`X-MedChat-Admin-Token`、`require_admin` 或 `MedChatAdminAuth` 的有效代码引用。
- 原受保护接口在不提供令牌时可按自身业务语义正常响应。
- 模型配置接口继续不返回 API Key 明文。
- Python、Agent、前端安全和 JavaScript 静态测试通过。
