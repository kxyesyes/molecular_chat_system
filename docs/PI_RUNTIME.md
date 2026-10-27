# Pi 运行与故障处理

MedChat 的默认 Agent 循环由官方 Pi 驱动，Python 保留模型事务、领域授权、科学执行与结果校验。
此文描述当前 main 的安装与边界。PR #217 已记录本地 Ollama 四项隔离 ChatHandler 验收通过；
这不代表生产部署、云端或完整浏览器科学闭环已完成验收。

**当前记录不构成生产替换依据。** 跨入口回归曾发现四工具准入遗漏旧 Web 能力；现以独立的
`medchat-web-v1` 领域契约恢复完整入口，生成、优化/排序、ADMET、反向寻靶、RAG、引用与审批链已有分组回归。
早期交付保留了真实模型未验证项；后续本地隔离验收结果见文末。完整 CI、真实云端、
完整 Web 流程及部署健康仍须通过各自发布门禁。
保留底层工具和确定性 API 本身不构成 Web 验收。详见[验证记录](superpowers/validation/2026-10-23-pi-harness-replacement.md)。

## 安装与启动

安装 Node.js **24.15.0**（本分支 CI 固定版本）；运行时最低要求 **22.19.0**。
用 `node --version` 确认终端实际使用的版本。在仓库根目录、已具备 Python 科学依赖的环境中执行：

```powershell
npm ci --prefix runtime/pi --ignore-scripts --no-audit --no-fund
python -m pip install -r requirements.txt
python main.py --port 6001
```

浏览器访问 `http://127.0.0.1:6001`。无需安装全局 Pi，也不要用全局 CLI 或 `npm update` 替代锁文件安装。
`runtime/pi/package.json` 与 `package-lock.json` 固定官方
`@earendil-works/pi-coding-agent` **1.4.0**；部署应携带同一发布版本的两份文件。
旧 `requirements-agent-harness.txt` 及 `requirements-ci.txt` 中对应的 include 已删除，不再安装旧 Python harness 钉住版本；RAG 依赖未因此删除。

`.env.example` 使用 `MEDCHAT_CHAT_PROFILE=pi`、`MEDCHAT_DECISION_WIRE_MODE=native`。
这是 Web 配置示例，不是下述隔离验收 CLI 的 provider-aware 默认值；CLI 省略 `--mode`
时的选择不会自动修改 Web 配置，也不证明 Web 已按同一协议通过验收。
Web 的 `normal_chat_mode` 默认也是 `pi`；`legacy` 会被拒绝，`decision_a2` 仅为迁移兼容别名，仍执行 Pi。
Pi 不可用时明确失败，**不自动回退**到旧 harness。分子生成器仍使用独立的 Ollama 模型配置，不被主模型覆盖。

默认 `pi` 选择 `medchat-web-v1`：复用确定性领域规划器生成不可变动作与依赖，Pi 每次只选择一个
`step_ref`，Python 将其绑定到现有工具。模型不能自由更改分子、数量、温度、靶点或输入证据。
`decision_a2` / `a1_closed` 保留较窄的兼容准入；`semantic_v1` 的 B1 权限不因新增 Web profile 自动扩大。
对接仍走独立的 C 审批路径，不进入通用十工具目录。

### Web 决策输入预算

完整 Web profile 会冻结 `inference.input_max_chars`（本地默认 12000）、
`external_input_max_chars`（外部默认 24000）、`history_input_max_chars`（默认 2000）
以及请求温度和输出预算。恢复等待会话沿用原请求设置；不会读取新配置替换已封存契约。
这些是应用字符限制，不是模型 token 窗口估算。最终检查计入协议系统消息、完整问题、
观察内容与原生函数参数文本；原生工具的固定 schema 不属于消息字符计数。

仅普通历史可以按完整 user/assistant 对从最旧轮次起省略，省略提示也计入总预算。
历史不在 requirements 提示中重复发送；原始封存契约和服务器保留历史不变。
问题、SMILES、科学证据、来源证明及协议纠错内容不截断。必要内容仍超限时，本次 HTTP
请求不会发出，也不追加重试；返回 `decision_input_budget_exceeded`。
若已有通过独立校验的科学结果，则明确以 partial 展示，审计区分服务器保留的结果与模型引用，
`satisfied` / `finish_eligible` 仍为 false。预算停止不代表此前科学任务未执行。

科学续接仅接受原请求重述、明确继续确认、完整 SMILES 或缺失的受支持活性靶点字段。撤销、改变数量、
新增约束或其它不能安全识别的补充不会静默套用原计划；应重新发起任务。已生成候选可在验证通过后复用，
不能因为恢复而重新生成。普通聊天续接不使用这套科学字段限制。

## 健康检查与离线契约

```powershell
python scripts/health_check.py --strict
npm test --prefix runtime/pi
```

新增的 `Pi Runtime` 检查验证 Node 最低版本、固定包版本、npm 锁文件及实际 SDK 导入；不创建会话，
不调用模型或科学工具。子进程限时 15 秒，丢弃 stdout/stderr，使用临时 home/config 目录与最小环境。
父进程凭据、代理变量、`NODE_OPTIONS`、`NODE_PATH`、Pi 配置路径不会传入探针；只显式启用离线模式。
安装或导入失败时按提示重新运行上述 `npm ci` 命令；缺失/不一致的 manifest 或锁文件应先从同一发布版本恢复。
不要清空数据库来解决 Node 或 SDK 安装问题。

整个 `--strict` 仍包含原有的 Ollama、科学依赖、数据、部署等检查；“Pi Runtime 通过”不等于全部健康检查通过。
Node 契约测试使用官方 SDK，但离线契约通过也不证明真实模型、工具或生产环境已可用。

## 隔离模型验收 CLI（显式运行才会调用模型）

入口为 [run_decision_chat_acceptance.py](../scripts/run_decision_chat_acceptance.py)。
`--provider` 支持 `cloud`（默认，保留既有云端配置）和 `ollama`；`--mode` 支持 `native` 和 `json`。
未显式指定 `--mode` 时，脚本按 provider 选择：`ollama` 默认 `json`，`cloud` 默认 `native`；
显式指定时使用指定值。此规则对应脚本 `main()` 的 CLI 参数处理。
该脚本不加载 `.env` 或用户配置文件，也不修改 Web 或分子生成配置。

本地 Ollama 默认使用 JSON 模式，也可显式传入 `--mode json`；必须通过 `OLLAMA_CHAT_MODEL` 选择本次聊天验收模型，
不会读取或修改分子生成器的 `OLLAMA_MODEL`，也不会从云端模型名推断本地模型。
PR #217 记录 `gmm-llama:latest` 在 JSON 模式下通过四项隔离验收；同一记录中显式 native 模式
因模型报告不支持 tools 而不可用，不能将 JSON 的结果外推到 native 或其他模型。以下命令省略 `--mode`，
用于选择 Ollama 默认的 JSON 协议（需本机已有服务与模型）：

```powershell
$env:OLLAMA_BASE_URL = 'http://127.0.0.1:11434'
$env:OLLAMA_CHAT_MODEL = 'gmm-llama:latest'
python scripts/run_decision_chat_acceptance.py --provider ollama
# 等价的显式协议选择：
python scripts/run_decision_chat_acceptance.py --provider ollama --mode json
```

`OLLAMA_BASE_URL` 未设置时默认 `http://127.0.0.1:11434`；仅允许 `localhost`、`127.0.0.1` 或 `::1`。
Web 与 CLI 共享的本地传输校验全部 DNS 结果均为回环地址，再固定数值连接目标；禁用代理环境继承和重定向，
保留 TLS 校验，不修改共享云端网络策略。无需云端 API key；适配器要求的 Authorization 使用内部固定占位值，
不读取或转发云端凭据。模型未安装、服务不可达或不支持协议都不能记为通过。

CLI 本地模式与 Web 的 `provider=ollama` 分支都构造 canonical `OllamaModel`，并由组装层显式注入决策协议。
`decide`、`propose_ordinary_intent`、`propose_docking_preparation` 各自只委托一次既有协议事务；内部短生命周期
OpenAI-compatible facade 使用原模型名/地址及共享回环传输，结束时等待其专用客户端关闭。
不复用或改写生成客户端、Cookie、Authorization；生成方法和独立分子生成器保持不变。
对接准备使用注入的 response guard，响应收尾先于客户端关闭；取消不跳过收尾，也不触发重试或旧循环回退。
此处验证的是默认 `pi` 路径；`semantic_v1` 与聊天对接准备既有的 approved-provider 准入限制
未随适配器方法增加而放宽，本地协议适配器存在不代表本地模型获准使用这些入口。

本地 DNS 解析在线程中执行，不阻塞事件循环。超时或取消后不再派发 HTTP；关闭客户端前仍等待
已启动的系统解析实际结束，重复取消也不会丢失资源所有权。因此超时是派发截止，不保证系统 DNS
异常时清理能在同一墙钟期限内返回。

云端模式仍需由运行进程显式提供 `OPENAI_COMPATIBLE_API_KEY`、`OPENAI_COMPATIBLE_BASE_URL` 和
`OPENAI_COMPATIBLE_MODEL`，不在文档、命令输出或报告中记录其值：

```powershell
python scripts/run_decision_chat_acceptance.py --provider cloud
# 等价的显式协议选择；这是操作示例，不是云端通过记录：
python scripts/run_decision_chat_acceptance.py --provider cloud --mode native
```

云端继续受可信域名、HTTPS、DNS 和 pinned-network 策略约束；脚本不自动添加可信主机。
固定诊断码 `decision_endpoint_rejected` 会保留在 `model_calls[].reason`，不包含原始 URL、DNS 地址或异常正文；
应核查获准环境的网络配置，不通过关闭策略绕过失败。

四个 case 覆盖普通聊天、两个分子的 RDKit 性质、澄清及续接、无效 SMILES 拒绝；每个 case 都要求
`checks.pi_runtime_backend=true`，澄清前后两轮均检查。无效输入 case 的“通过”表示正确拒绝且未伪造性质，
并不表示科学计算成功。报告范围仅为 **isolated ChatHandler entry**，不是完整浏览器、部署、所有工具或
云端/本地科学闭环验收；真实模型仍可能不符合协议。

默认在 `outputs/agent_evaluation/` 创建带唯一名称的 JSON 报告；`--output` 可指定新的本地 `.json` 文件。
保留独占创建、不覆盖已有文件、拒绝符号链接/reparse point、设备/流路径等检查；临时状态库在结束后删除。
缺少配置时为 `skipped`（本地缺模型名为 `missing_ollama_chat_model`），不可用或检查失败为 `failed`/`partial`，
全部通过才退出 `0`，其余退出 `2`。只记录固定诊断与脱敏检查，不输出密钥、原始响应或配置值。

## 凭据、权限与进程生命周期

主模型仍通过既有 Python `model.decide` 单次事务与 pinned-network 传输访问；配置和凭据留在 Python 的受控边界。
API key 不传入 Node 环境、协议 stdout 或 Pi session 文件。不要开启输出原始 JSONL、提示或响应的调试日志。
Pi 使用内存会话；内置文件/终端工具、自动扩展、技能与项目指令发现均关闭，仅暴露 Python 授权的领域工具。
不通过 Node 提供新的科学计算、模型网络或任意命令执行入口。

每次运行拥有独立 Pi 子进程并在结束时关闭。关闭/终止该子进程**不等于科学任务已取消**：
已开始的科学 worker 仍由领域调用方负责取消、等待实际结束与资源排空，不能据进程退出直接发布“已取消”终态。

## Checkpoint 与回滚

新 Pi 协议使用 `pi_protocol_revision=1`；当前领域 snapshot revisions 为 **9/10/11/13**。
Web profile 使用 revision **13**，B1 保持 **11**；旧实验 Web revision **12** 保留作审计，拒绝执行恢复。
不允许跨 profile 隐式升级或混合重放。
续接必须通过 Pi 历史、授权、输入、观察、来源和版本校验，不能把历史文本或模型声明视作执行证据。
旧 checkpoint 仍可读取用于历史审计，但不再允许执行续接；不删除旧记录，不清库，也**不自动转换**旧 checkpoint。
用户需要在当前版本重新发起任务，而不是强行重放旧科学动作。

确认分子的重复引用请求使用独立的持久化认领表 `agent_reference_requests`。同一用户/会话、
完整来源指针、问题、编译动作、权限及配置形成请求身份；新的传输 trace 和增长的聊天历史不产生新身份。
首次上下文会被冻结，重试仍由 Pi 选择动作，Session 仅复用通过输入/输出摘要、来源、版本和绑定证明校验的回执。
取消后真实完成并通过校验的工具结果可以保留；公开回合仍显示取消，不补发科学成功事件。

原工作线程、来源检查或发布收尾尚未确认时，不开放重试；不根据超时抢占，也不把进程退出当成工具停止。
等待用户补充的请求保留原认领，只能通过既有一次性 continuation nonce 恢复。损坏回执或无法确认的发布状态
失败关闭，不清除数据库、不把解码失败当作重新执行的许可。新增表为增量建表，不改写历史 checkpoint。
已发布候选的请求重试时，在同一事务中把原运行完整记录写入只追加的 `agent_reference_run_history`，
再消费新一代认领并撤销当前运行的旧展示指针。旧确认不会自动复活；新展示仍须重新挂载确认。
原始输入来源、原展示到期时间及历史 checkpoint 不改写。归档、认领或替换任一步失败均整体回滚；
损坏的历史元数据拒绝重开，而不是删除后重试。

回滚以完整 Git 发布版本为单位，同时恢复对应 Python 代码/依赖、Node 版本约束及 npm manifest/锁文件，
再按对应版本执行 `npm ci`。不得仅切回 profile、只降级一个 npm 包或混搭新旧 Python/Node 运行时。
保留数据库与审计记录；跨版本 checkpoint 不自动转换，是否可续接由目标发布版本自身的校验决定。

## 实测状态与尚待验收

### 历史失败（保留）

2026-10-23 的有界实测未通过：云端四项在科学执行前被 `decision_endpoint_rejected` 拒绝，
本地 `gmm-llama:latest` 的 JSON 四项出现超时、不合规决策 schema 和无效输入引用。
没有绕过网络/领域保护，也没有用 mock 或伪造 RDKit 结果补齐。详见[准确验收记录](superpowers/validation/2026-10-23-pi-harness-replacement.md)。

### 后续本地隔离验收（PR #217）

[PR #217](https://github.com/kxyesyes/molecular_chat_system/pull/217) 于 2026-10-25 合并到 main
（合并提交 `d56143b18619d76cb9f0d599a7f5ce59ddf48d9f`）。其 Validation 记录使用真实本地模型
`gmm-llama:latest`、Ollama JSON 模式完成 **4/4** 项验收；此后续记录更新了本地隔离测试状态，
不删除或改写上述历史失败。本节转述已合并 PR 的验证记录，不表示在当前 main 上重新执行了真实模型验收。

| 用例 | PR #217 记录的结果 | 范围 |
| --- | --- | --- |
| CHAT-001 普通聊天 | 通过 | 能力介绍完成，未调用科研工具 |
| CHAT-002 双分子性质 | 通过 | CCO / CCN 的 RDKit 性质计算 |
| CHAT-003 缺失 SMILES | 通过 | 先澄清，补充 SMILES 后成功续接 |
| CHAT-004 无效 SMILES | 通过 | 安全拒绝，未返回伪造性质；不代表计算成功 |

当前脚本的报告范围明确为 **isolated ChatHandler entry**，使用临时状态库及仅含
`property_calculator` 的工具注册表。四项通过不覆盖完整 Web profile 的工具、授权与浏览器流程，
也不证明云端、服务器部署或生产环境已通过验收。PR #217 同时保留了
`gmm-llama:latest` 显式 native 模式不支持 tools 的限制。

### 仍待验收与健康检查限制

- 云端真实模型验收仍为 **未验证**；现有记录是上述 `decision_endpoint_rejected` 失败，
  本地 JSON 成功不能替代云端证据。
- 完整浏览器科学闭环、服务器/生产部署，以及部署环境中的断连、取消、资源排空和续接仍须独立验收。
  CHAT-003 仅证明所记录的隔离澄清续接场景，不能覆盖这些完整生命周期场景。
- [PR #218](https://github.com/kxyesyes/molecular_chat_system/pull/218) 的后续 Validation 记录中，
  严格健康检查仅 **19/24** 项通过；剩余检查需要 Ollama/Pi 服务、ACL 访问或反向寻靶数据。
  该记录不是全部健康检查通过，也不构成当前部署可用性保证。

健康检查、mock 测试或 SDK 离线契约测试均不替代真实验收；局部模型/工具成功也不证明模型分数或科学结论有效。
