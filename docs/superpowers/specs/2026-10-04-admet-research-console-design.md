# ADMET Research Console 设计规格

日期：2026-10-04  
状态：待用户确认后实施

## 1. 目标

将当前静态的 KERMT ADMET 演示页替换为一个可追溯的 ADMET 研究控制台：用户提交 SMILES 后，页面只展示后端真实工具返回的结果、来源、模型状态、警告和失败原因。页面不生成、不猜测、不保留任何硬编码科学数值。

新页面使用 `/admet` 作为唯一入口。本轮明确移除 `/kermt-admet` 路由及其导航入口，不保留兼容重定向；旧模板文件可以暂时保留为未使用资产，后续清理另行处理。

## 2. 现状与问题

- `src/web/templates/kermt_admet.html` 是模板、样式和脚本混合的单文件页面。
- 表单提交后仅等待定时器，随后展示固定的 Caco-2、HIA、BBB、CYP3A4、hERG、AMES 数值。
- 这些数值没有请求 ID、工具 provenance、模型/权重标识或失败状态，不能作为科研结果展示。
- `/api/molecule/properties` 目前只做 RDKit 基础性质，ADMET 字段明确为 `unavailable / not_calculated`，不能被页面误当成 ADMET 预测。
- 真实 ADMET 工具是 `src.agent.tools.admet_predictor.ADMETPredictor`，其结果带有逐分子状态、`prediction_method`、模型版本、权重标识、`demo_mode`、`fallback_used`、warnings 和 provenance。

## 3. 视觉与交互方向

页面采用“研究控制台”而不是营销式预测卡片：

- 深石墨背景与浅色内容层，使用青绿色表示已验证/成功，琥珀色表示警告，冷灰表示未计算，红色表示失败。
- 重点突出输入结构、评估状态和证据来源，而不是用大号彩色数字制造确定性。
- 桌面端为三栏：左侧输入与运行控制，中间结果与端点分组，右侧证据/状态侧栏；窄屏时按输入 → 结果 → 证据顺序堆叠。
- 页面保留无障碍语义、键盘操作、明显焦点态和 `prefers-reduced-motion` 支持。
- 不新增前端框架、打包器或第三方 CDN；沿用 Jinja2、原生 JavaScript、现有静态资源方式。

## 4. 页面结构

### 4.1 顶栏

- 页面标题：`ADMET 研究控制台`。
- 副标题：说明结果来自本地 ADMET-AI 或明确的不可用状态，不等于实验结论。
- 返回 MedChat 首页。
- 服务状态徽标：`就绪`、`运行中`、`部分完成`、`不可用`、`失败`。

### 4.2 输入面板

- 单分子 SMILES 输入框，保留示例分子按钮。
- 显示规范化后的输入摘要，但不擅自替换用户结构。
- 提交按钮、清空按钮和加载状态。
- 输入为空或结构无效时在本地给出明确提示；仍以服务端完整校验为准。
- 页面不承诺“预测一定可用”，提交前提示模型缺失会返回不可用状态。

### 4.3 结果区

- 顶部显示本次运行状态、分子 ID、输入 SMILES 和总体消息。
- 端点分组：物化性质、吸收、分布、代谢、排泄、毒性、药物相似性。
- 每个端点显示：名称、值、单位、端点状态、来源类型和必要的解释。
- 状态至少支持：`succeeded`、`partial`、`failed`、`unavailable`、`not_calculated`。
- 缺失或失败端点显示“未计算/不可用/失败原因”，禁止填充 0、随机值或旧结果。
- 多分子响应按真实 `molecule_id` 分卡；失败分子仍保留失败卡，不丢失批处理上下文。

### 4.4 证据侧栏

- 工具：`admet_predictor`。
- 模型名称、版本、权重标识、prediction method。
- `demo_mode` 与 `fallback_used` 状态；若任一不满足真实模型要求，显示警告而不是绿色成功。
- 输入/输出摘要、warnings、error/reasoning、provenance 摘要。
- 明确提示：模型预测不等于实验结果；RDKit 物化性质与模型端点分开标注。

### 4.5 空、加载、失败状态

- 首次进入：说明输入格式和输出边界，不展示任何虚构结果。
- 加载中：显示正在执行的阶段和取消/重新提交入口（如果后端接口支持取消；否则不显示不可用按钮）。
- 部分完成：保留成功端点，显式列出失败端点。
- 不可用：显示模型/依赖不可用原因和下一步建议。
- 无效输入：只显示结构校验错误，不渲染任何性质或 ADMET 数值。

## 5. 数据与接口契约

### 5.1 页面数据源

页面不再消费旧模板中的静态结果，也不把 `/api/molecule/properties` 的 `admet: Unknown` 当成预测。

实施阶段优先增加一个薄的专用接口，例如 `POST /api/admet/predict`，内部复用现有 `ADMETPredictor`，不复制预测逻辑。请求至少包含：

```json
{
  "smiles": "CCO",
  "molecule_id": "molecule-001"
}
```

响应应保留现有工具契约中的 `success`、`status`、`message`、`data`、`warnings`、`quality`、`provenance`、`reasoning`；专用接口只负责输入校验、调用、资源清理和安全的 HTTP 映射。

如果实施前发现已有等价的真实 ADMET HTTP 入口，则复用该入口并在实现计划中明确证据，不新增重复接口。无论采用哪种入口，都不得通过主聊天模型生成科学数值。

### 5.2 真实性规则

- `prediction_method=admet_ai`、`demo_mode=false`、`fallback_used=false` 才能标记为真实模型成功。
- backend 不可用、超时、输入无效、逐分子失败必须原样映射为 `unavailable`/`failed`/`partial`。
- 不允许前端根据数值自行推导“安全”“有效”“可成药”等实验结论。
- 前端只格式化受控字段，并对文本使用安全 DOM API 或统一转义 helper。

## 6. 路由与文件边界

计划新增/修改：

- `src/web/routes/main_routes.py`：新增 `/admet`，删除 `/kermt-admet`。
- `src/web/routes/page_routes.py`：页面路径集合改为 `/admet`，不保留旧路径。
- `src/web/templates/index.html`：两个 ADMET 导航入口改为 `/admet`。
- `src/web/templates/admet.html`：新的语义化页面模板。
- `src/web/static/css/admet.css`：页面样式，避免内联大段 CSS。
- `src/web/static/js/admet.js`：请求、状态机、结果渲染和无障碍交互。
- `src/web/routes/admet_routes.py` 或已有 API 路由模块：仅在确认无等价入口时增加薄 API 适配。
- `tests/`：新增页面路由、API 契约和前端静态安全/状态渲染测试。

不在本轮范围：

- 重写 ADMET-AI 模型、训练权重或科学阈值。
- 修改 Agent 路由/Planner/Workflow 架构。
- 把外部主模型接入 ADMET 数值计算。
- 引入 React、Vue、npm 构建链或新的数据库。
- 删除旧 `kermt_admet.html` 文件；本轮只移除其 HTTP 入口和导航引用。

## 7. 验证计划

- 路由测试：`/admet` 可访问，`/kermt-admet` 不注册。
- API 测试：真实工具成功、不可用、无效 SMILES、部分批处理状态均能透传。
- 前端静态测试：无硬编码 ADMET 科学数值、无不受控 `innerHTML`/inline handler；结果字段安全渲染。
- JS 语法：`node --check src/web/static/js/admet.js`。
- Python：相关 `pytest` 与 `python -m compileall -q src scripts`。
- 现有首页与 Agent 回归不得因导航改路径而失败。

## 8. 设计自检

- 是否会把模型不可用误显示为成功：不会，状态由响应契约驱动。
- 是否会丢失失败分子或 warning：不会，逐分子 data 与顶层 warnings 均保留。
- 是否会保留旧 `/kermt-admet`：不会，按用户要求删除路由与导航入口。
- 是否会产生第二套 ADMET 科学逻辑：不会，API 仅适配现有 `ADMETPredictor`。
- 是否需要待确认项：专用 API 是否新增，需在实现前根据现有 HTTP 入口最终确认；若无等价入口，采用本文薄适配方案。
