# MedChat 运行时边界与模型装配整理设计

## 目标

在不改变现有业务模块、技术栈、公开接口、算法结果和任务清理语义的前提下，先解除科研业务与 Agent 对 `src.web` 的反向依赖，并统一模型客户端、网络策略和请求生命周期的装配边界。

本设计是渐进式整理的第一阶段。它不重写固定工作流、模型决策循环或任务运行时，也不同时处理 `api_routes._support`、科研结果契约和前端拆分。

## 当前问题

已确认的跨层依赖如下：

- `src/molecular_design/service.py` 直接导入 `src.web.model_lifecycle.model_request`。
- `src/agent/openai_compatible_model.py` 直接导入 `src.web.security.url_policy.validate_llm_url`。
- `src/agent/decision_transport.py` 直接导入 `src.web.security.url_policy.resolve_llm_host`。
- `src/agent/tools/llm_molecular_generator.py`、`src/agent/tools/__init__.py` 和 `src/agent/evaluation/scientific.py` 直接导入 `src.web.models.ollama_model`。
- `src/task_runtime/routes.py` 依赖 `src.web.api_response`。该依赖属于后续任务运行时/HTTP 适配整理，不纳入第一阶段。

模型创建目前主要集中在 `src/web/app.py`，但具体客户端实现、生命周期门禁和安全网络策略分散在 Web、Agent 和模型模块中，导致核心模块不能独立导入和测试。

## 设计方案

### 1. 使用现有 `src/system/` 作为中立边界

不新增平行架构或新的顶层目录。在现有 `src/system/` 下增加少量共享模块：

- `model_clients.py`：承载 Ollama 和 OpenAI-compatible 客户端的实现或稳定导出。
- `model_lifecycle.py`：承载模型请求门禁、取消后的资源持有和关闭辅助函数。
- `network_policy.py`：承载模型出站 URL 校验和解析策略。

具体文件名和拆分以现有测试约束为准；如果一个中立模块足以表达职责，不再继续拆分。

### 2. 保留兼容导入路径

现有路径继续可用，但只作为兼容导出：

- `src/web/models/ollama_model.py` 继续导出 `OllamaModel` 及其错误类型。
- `src/agent/openai_compatible_model.py` 继续导出 `OpenAICompatibleModel`。
- `src/web/model_lifecycle.py` 继续导出 `ModelRequestGate`、`model_request`、`finish_on_cancel` 等公开对象。
- `src/web/security/url_policy.py` 继续导出现有 Web 侧需要的函数。

兼容导出必须指向同一对象，不能复制一套实现。这样既保持旧调用方可用，也能让科研核心和 Agent 依赖中立模块。

### 3. 统一装配与生命周期

`MolecularChatApp` 继续作为应用组合根，负责：

- 根据已有配置创建主模型和分子生成模型；
- 创建并传递同一请求门禁及共享资源；
- 通过已有 `AsyncExitStack` 注册拥有的资源；
- 在应用关闭、装配取消或模型切换时执行一次且仅一次关闭；
- 区分应用拥有的客户端和外部借用客户端，服务层不得擅自关闭借用资源。

本阶段不引入新的执行器、后台任务系统或替代 `task_runtime` 的生命周期。现有 `ModelRequestGate` 的并发、独占、取消等待和错误屏蔽语义保持不变。

### 4. 依赖方向

目标依赖方向为：

```text
src/system  ←  src/agent / src/molecular_design / src/rag / src/docking
     ↑
   src/web  （组合、HTTP 适配、兼容导出）
```

`src.web` 可以依赖 Agent 和科研核心来提供 HTTP/Agent 入口；科研核心和 Agent 核心不得反向导入 `src.web`。本阶段只处理已确认的模型、生命周期和网络策略边界，不把所有 Web 依赖一次性搬迁。

## 保持不变的行为

- 公开类名、函数名、旧导入路径和已有构造参数。
- Ollama、OpenAI-compatible、ModelScope 的请求格式、流式协议、错误状态和元数据字段。
- URL 安全校验、DNS 固定策略、禁止代理绕过和敏感信息脱敏。
- 模型请求门禁、取消清理、后台任务持有和应用关闭顺序。
- 固定工作流、Supervisor/决策循环、任务运行时和科学算法。
- 科研结果的成功、部分成功、失败和来源状态，不把部分结果提升为成功。

## 测试策略

先建立并保持以下测试：

1. 导入边界测试：目标 Agent/科研核心模块的源码或导入图不再依赖 `src.web`。
2. 兼容性测试：旧 Web/Agent 导入路径与中立模块导出的对象身份一致。
3. 模型请求测试：同步、异步、流式调用的请求体、错误响应和元数据保持等价。
4. 生命周期测试：应用拥有的客户端关闭一次；借用客户端不被错误关闭；取消路径最终释放门禁和后台持有。
5. 现有模型、决策传输、分子生成和分子设计聚焦回归。

完成第一阶段后再运行完整 Python 回归、`compileall` 和现有 Node 检查。失败必须区分环境缺依赖、既有失败和本次回归。

## 后续阶段边界

### 第二阶段：替换 `_support`

按路由组逐批把模块对象依赖替换为显式、最小的路由依赖对象；保留旧 `setup_*_routes` 调用兼容层，先迁移无科学状态的响应、文件和限制辅助函数，再处理任务提交和资源生命周期。每批保留真实 HTTP 回归。

### 第三阶段：科研核心与入口适配分离

为 ADMET、活性、对接、靶点、分子设计等模块明确业务输入/结果契约。HTTP 路由只做认证、输入解码和响应映射；Agent 只做工具适配和授权，不复制科学计算。错误、来源、部分结果和取消状态统一映射。

### 第四阶段：前端职责拆分

在不更换框架和视觉结构的前提下，分别整理连接层、协议层、任务状态层和渲染层。前端状态只能由后端任务状态和结果状态映射得到，不再用时间模拟进度或把展示状态当作科研成功状态。

## 风险与控制

- 兼容导出可能出现循环导入：先移动底层依赖，再改调用方，最后保留单向兼容导出。
- 客户端关闭责任可能重复：为每个 owner/borrrower 场景增加计数测试，沿用现有 `AsyncExitStack`。
- 安全策略迁移可能弱化校验：旧 Web 安全测试必须保持通过，并增加中立模块直接调用测试。
- 运行环境缺少 RDKit、PyTorch 等科学依赖时，只允许报告环境阻塞，不用替代结果冒充通过。

## 第一阶段完成判定

- 目标核心模块不再反向导入 `src.web`。
- 旧公开导入路径继续可用且对象身份兼容。
- 模型客户端和生命周期只有一份实际实现，应用组合根负责拥有关系。
- 聚焦回归和静态边界检查通过，未改变现有科学输出和任务清理行为。
- 变更拆为可独立回退的小提交，并记录实际减少的跨层边数、迁移职责和剩余依赖。
