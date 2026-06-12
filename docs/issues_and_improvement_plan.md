# 分子聊天系统问题定位与改进清单

最后更新: 2026-04-14

## 1. 关键问题总览

本清单面向当前目标约束:
- 目标用户: 主 A(药化研究员) 次 B(计算化学工程师)
- 协议策略: 先可调整，后续版本化并冻结
- 输出模式: LLM 文本流式 + 结构化计算非流式（混合输出）
- 会话状态: 当前内存，预留 Redis 扩展点
- 部署路径: 当前 Windows 开发，目标 Linux 运行
- 性能目标: 基础计算 <3s 同步；对接/反向寻靶异步任务化

---

## 2. 问题清单（含位置与改进方向）

### P0-1 Agent 返回字段与 WebSocket 消费字段不一致
- 位置:
  - src/web/chat_handler.py:124
  - src/web/chat_handler.py:126
  - src/agent/agent_executor.py:163
  - src/agent/agent_executor.py:164
- 现象:
  - ChatHandler 读取 final_answer/tools_used。
  - MolecularAgent 返回 response/used_tools。
- 风险:
  - 工具执行成功但结果未被主回答正确融合，出现“工具已完成但内容缺失”。
- 改进:
  - 统一 AgentResult 契约（例如 success, text, tools, metadata, error）。
  - ChatHandler 仅消费统一字段，禁止散落式 get() 兼容逻辑。

### P0-2 前端调用模型切换接口但后端未提供
- 位置:
  - src/web/static/js/script.js:432
  - 全库未发现 /api/switch_model 路由定义（仅存在模型管理器方法）
- 现象:
  - 前端 fetch('/api/switch_model')，后端未注册对应接口。
- 风险:
  - 用户可见功能与后端能力不一致，形成“假按钮”。
- 改进:
  - 新增 POST /api/switch_model（受控白名单 + 状态回执 + 当前模型查询接口）。
  - 或暂时隐藏前端模型切换控件，直到后端接口就绪。

### P0-3 会话历史为全局内存列表，存在跨连接串话
- 位置:
  - src/web/chat_handler.py:23
  - src/web/chat_handler.py:233
- 现象:
  - 单一 self.conversation_history 对所有 websocket 连接复用。
- 风险:
  - 多用户上下文污染，潜在隐私泄露。
- 改进:
  - 引入 SessionStore 抽象（MemorySessionStore -> RedisSessionStore）。
  - key 采用 session_id/connection_id；设置 TTL 与最大轮数。

### P0-4 重计算在 async 路由内同步执行，阻塞事件循环
- 位置:
  - src/web/routes/api_routes.py:19
  - src/web/routes/api_routes.py:64
  - src/docking/molecular_docking_service.py:122
  - src/docking/molecular_docking_service.py:310
  - src/docking/molecular_docking_service.py:516
- 现象:
  - 对接流程由 async API 直接 await，内部使用 subprocess.run。
- 风险:
  - WebSocket 流式与 HTTP 并发性能下降，长耗时时系统响应变差。
- 改进:
  - 对接/反向寻靶改为异步任务队列（job_id + 状态机 + 进度推送）。
  - API 改为 submit/query/result 三段式。

### P0-5 密钥硬编码（高风险）
- 位置:
  - config/modelscope_config.yaml:6
  - src/agent/tools/rxn_chemistry_agent.py:27
- 现象:
  - ModelScope API key 与 RXN API key 明文存储于仓库文件。
- 风险:
  - 泄露后可被滥用，且无法合规审计。
- 改进:
  - 使用环境变量 + 本地密钥文件（不入库）+ 启动时校验。
  - 提供 config/modelscope_config.template.yaml 模板。

### P1-1 WebSocket 路由定义存在重复入口
- 位置:
  - src/web/app.py:409
  - src/web/routes/websocket_routes.py:15
- 现象:
  - /ws 在主应用与路由模块都有定义。
- 风险:
  - 路由管理分叉，后续维护容易出现行为不一致。
- 改进:
  - 收敛为单一注册入口（推荐在 routes 模块集中注册）。

### P1-2 OllamaModel 存在多份实现，职责重复
- 位置:
  - src/web/app.py:37
  - src/web/models.py:13
  - src/web/models/ollama_model.py:26
- 现象:
  - 同名模型类散布多个模块，行为参数不一致。
- 风险:
  - 调优与修复无法一次生效，易引入回归。
- 改进:
  - 统一模型层接口与实现位置；其他位置改为导入同一实现。

### P1-3 agent 层直接依赖 src.web.app，耦合过高
- 位置:
  - src/agent/react_agent.py:49
  - src/agent/tools/__init__.py:29
- 现象:
  - agent/tools 从 web app 导入 OllamaModel。
- 风险:
  - 架构层级反向依赖，容易触发循环依赖和初始化副作用。
- 改进:
  - 提取独立模型适配层（例如 src/llm/providers/*）。
  - agent 与 web 共同依赖该层，不互相依赖。

### P1-4 临时文件清理路径在异常分支可能泄漏
- 位置:
  - src/web/routes/api_routes.py:46
  - src/web/routes/api_routes.py:75
  - src/web/routes/api_routes.py:89
  - src/web/routes/api_routes.py:91
- 现象:
  - 临时文件主要在 try 尾部清理；中途异常会跳过清理逻辑。
- 风险:
  - 临时目录膨胀，长期运行后磁盘占用增长。
- 改进:
  - 改为 try/finally 统一清理，或使用上下文管理器。

### P1-5 鉴权/CORS/统一异常治理尚未建立
- 位置:
  - src/web/app.py:1（应用入口中未见 CORS 与鉴权中间件注册）
- 现象:
  - API 默认裸露；错误返回格式在各路由中分散定义。
- 风险:
  - 安全边界不清晰，前端处理异常复杂。
- 改进:
  - 增加 CORS 白名单、鉴权依赖（可选开关）、统一错误模型与全局异常处理。

### P2-1 心跳机制为单次 ping/pong，缺少周期性保活
- 位置:
  - src/web/static/js/script.js:223
- 现象:
  - 建连后只发送一次 ping，未见固定周期心跳。
- 风险:
  - 长连接在代理/负载均衡场景下可能无感断开。
- 改进:
  - 增加 setInterval 心跳 + 服务端超时踢连接 + 客户端重连退避。

---

## 3. 与目标约束对齐的改进清单

### A. 混合输出协议（先可调整，后续冻结）
1. 引入 envelope: version, request_id, session_id, event_type, payload。
2. 文本流式事件:
   - llm.delta
   - llm.complete
3. 结构化非流式事件:
   - tool.result
   - task.progress
   - task.done
   - task.error
4. 发布 v0 -> v1 迁移策略（兼容期 + deprecation 字段）。

### B. 会话状态抽象（先内存，预留 Redis）
1. 设计 SessionStore 接口:
   - get(session_id)
   - append(session_id, message)
   - trim(session_id, max_turns)
   - delete(session_id)
2. 提供 MemorySessionStore 实现（当前默认）。
3. 预留 RedisSessionStore 实现点与配置开关。

### C. 异步任务化（对接/反向寻靶）
1. 新增任务提交接口:
   - POST /api/tasks/docking
   - POST /api/tasks/reverse-target
2. 新增任务查询接口:
   - GET /api/tasks/{job_id}
   - GET /api/tasks/{job_id}/result
3. 前端采用轮询或 websocket 订阅进度。
4. 基础 RDKit 快速计算继续同步返回（<3s）。

### D. 安全与配置治理
1. 删除仓库内硬编码密钥，改环境变量读取。
2. 提供 *.template.yaml，不含真实密钥。
3. 增加启动时密钥检查与错误提示（不打印密钥值）。
4. 增加 Linux 部署时的路径兼容检查（分隔符、可执行文件路径）。

### E. 可观测性
1. 所有请求统一 request_id。
2. 日志标准字段: session_id, job_id, elapsed_ms, error_code。
3. 指标: ws 连接数、任务队列长度、平均响应时间、失败率。

---

## 4. 建议实施优先级（可执行）

### 第 1 周（必须）
1. 修复 AgentResult 字段不一致。
2. 下线或补齐 /api/switch_model。
3. 清理明文密钥并替换为环境变量。
4. 会话按 session_id 隔离（内存实现）。

### 第 2 周（高优先）
1. 对接/反向寻靶改造为异步任务接口。
2. 引入协议 envelope 与版本字段。
3. 统一 OllamaModel 实现，消除重复定义。

### 第 3 周（增强）
1. CORS/鉴权与全局异常治理。
2. 周期性心跳与连接超时策略。
3. Linux 目标环境验证脚本与部署文档补齐。

---

## 5. 验收标准（建议）

1. 多用户并发压测下，不出现会话串话。
2. 对接/反向寻靶不会阻塞聊天流式响应。
3. 前端模型切换行为与后端接口一致。
4. 仓库中无真实密钥。
5. 新旧协议兼容期内消息可被前端正确解析。
