# 首页与活性预测脚本模块化整理设计

日期：2026-04-22  
范围：前端脚本结构整理与模块化拆分  
涉及文件：
- `src/web/static/js/script.js`
- `src/web/static/js/activity_prediction_v2.js`
- `src/web/templates/index.html`
- `src/web/templates/activity_prediction.html`

## 1. 目标

将首页聊天脚本与活性预测脚本从“超大单文件”整理为和现有项目一致的模块化结构，降低维护成本，并在不主动改变现有功能行为的前提下提升以下方面：

- 文件职责清晰度
- 代码可读性
- 功能定位速度
- 后续迭代可维护性
- 与现有 `design / docking / reverse_target` 目录结构的一致性

本次工作的核心不是功能升级，而是结构收敛与边界重建。

## 2. 约束

- 允许拆分为多个新 JS 文件。
- 允许修改模板中的脚本引用顺序。
- 保留原始大文件作为备份，不直接删除。
- 不引入新的前端框架。
- 不切换到 `type="module"`。
- 继续采用普通 `<script>` 加载方式，与现有项目保持一致。
- 第一轮整理以“机械拆分 + 命名统一 + 依赖显式化”为主，不主动修改业务行为。
- 如果拆分过程中发现明显死代码或重复代码，只在不改变行为的前提下做最小清理。

## 3. 现状问题

### 3.1 `script.js`

当前文件约 3265 行，混合了多种职责：

- 页面初始化
- 首页主题切换
- WebSocket 连接与重连
- 聊天消息渲染
- 内容格式化
- 分子结构与属性展示
- RAG 结果渲染
- 快捷入口逻辑
- 高级选项面板
- 动态注入样式

问题在于：

1. 文件职责过多，入口、状态、渲染、协议处理全部混在一起。
2. 很多函数依赖共享外层变量，边界不清。
3. 聊天渲染、分子渲染、内容格式化高度耦合，单独修改风险高。
4. 初始化流程不可读，页面问题不容易定位到具体责任块。

### 3.2 `activity_prediction_v2.js`

当前文件约 1332 行，虽然规模小于 `script.js`，但同样存在职责叠加：

- 单分子 / 批量预测
- 结果摘要渲染
- 训练前检查
- 启动摘要弹窗
- 训练监控抽屉
- ECharts 图表配置
- 模型管理
- 配置导入导出

问题在于：

1. 预测结果渲染与训练监控逻辑混在一个入口文件中。
2. 图表配置、模型状态、弹窗逻辑复用困难。
3. 多个区域共享全局变量，难以判断依赖链。
4. 后续继续加功能会让文件进一步膨胀。

## 4. 对齐基线

项目中已有较成熟的模块化模式：

- `src/web/static/js/design/`
- `src/web/static/js/docking/`
- `src/web/static/js/reverse_target/`

这些目录具备共同特点：

1. 使用多个普通脚本文件顺序加载，而不是单文件堆叠。
2. 每个文件职责明确，如 `main / ui_manager / api_client / config / state`。
3. 页面入口文件只负责初始化、绑定和串联，不承载全部实现细节。
4. 使用命名空间对象或全局模块对象进行协作，兼容当前模板加载方式。

本次整理将复用这套思路，不另起一套架构。

## 5. 设计方案

本次采用“命名空间模块拆分”方案。

### 5.1 原则

- 每个文件只承担一种主要职责。
- 模块之间通过全局对象协作。
- 模板中通过显式的脚本顺序保证依赖关系。
- 入口文件只负责初始化、事件绑定、模块协调。
- 状态集中，渲染分离，协议逻辑分离。

### 5.2 命名风格

采用与现有项目一致的轻量全局命名空间风格：

- 首页聊天页使用 `Home*`
- 活性预测页使用 `Activity*`

示例：

- `HomeState`
- `HomeTheme`
- `HomeSocket`
- `HomeRenderer`
- `ActivityModels`
- `ActivityCharts`
- `ActivityTraining`

## 6. `script.js` 拆分设计

新目录：

- `src/web/static/js/home/`

目标结构：

### 6.1 `config.js`

职责：

- WebSocket 默认配置
- 本地存储 key
- 连接重试参数
- 默认高级选项值
- 与模板文案无关的常量

不承担：

- DOM 查询
- 业务流程控制

### 6.2 `state.js`

职责：

- 集中保存运行时状态
- DOM 缓存对象
- `ws`、连接状态、聊天模式、RAG 开关、工具开关
- 当前消息列表
- 重连次数

要求：

- 所有跨模块共享状态从这里读写
- 避免继续在多个文件里各自声明散落变量

### 6.3 `theme.js`

职责：

- 首页主题切换
- 主题按钮状态同步
- 本地存储恢复

边界：

- 不处理聊天逻辑
- 不处理页面初始化以外的 UI 注入

### 6.4 `formatters.js`

职责：

- 文本格式化相关逻辑
- 合成路线格式化
- 反应预测格式化
- 文献结果格式化
- ADMET/类药性/分子性质/ReAct 等展示文本格式化

要求：

- 纯函数优先
- 不直接操作 WebSocket
- 尽量避免修改共享状态

### 6.5 `molecule_renderer.js`

职责：

- SMILES 结构渲染
- RAG 分子卡片渲染
- 分子属性展示
- 分页控件
- 分子复制与二次分析入口

要求：

- 与格式化模块解耦
- 将分子卡片渲染和普通聊天消息渲染边界拉开

### 6.6 `chat_renderer.js`

职责：

- 用户消息渲染
- 助手消息渲染
- typing/status/tool-status/error 等 UI
- 聊天容器初始化
- 滚动到底部

要求：

- 所有聊天消息 DOM 构造收拢到这里
- 页面状态提示类 UI 也归这里统一管理

### 6.7 `advanced_options.js`

职责：

- 高级选项弹层
- slider 联动
- 默认值恢复
- 配置保存与读取

要求：

- 与聊天主逻辑分离
- 对外暴露获取当前高级配置的方法

### 6.8 `ws_client.js`

职责：

- 建立 WebSocket
- 心跳 / 测试消息
- 重连逻辑
- 消息接收与分发
- 与连接状态有关的 UI 协调

要求：

- 协议处理逻辑集中到这里
- 不直接承担具体 DOM 渲染细节，尽量通过渲染模块完成

### 6.9 `main.js`

职责：

- 页面初始化
- DOM 查询和缓存
- 事件绑定
- 首页快捷入口
- 模型切换处理
- 调用各模块完成页面装配

要求：

- 成为唯一初始化入口
- 不再承载具体复杂渲染实现

### 6.10 旧文件处理

保留：

- `src/web/static/js/script.legacy.backup.js`

模板改为按顺序引用新目录中的脚本文件，不再直接引用 `script.js` 作为运行入口。

## 7. `activity_prediction_v2.js` 拆分设计

新目录：

- `src/web/static/js/activity_prediction/`

目标结构：

### 7.1 `utils.js`

职责：

- 数字格式化
- 状态标签 helper
- 通用文本设置 helper
- 活性区间基础常量

### 7.2 `model_manager.js`

职责：

- 加载模型列表
- 当前模型信息读取
- 当前任务类型判断
- 顶部模型信息与任务类型同步
- 删除模型逻辑
- 模型信息弹层内容更新

### 7.3 `results_renderer.js`

职责：

- 单分子预测摘要卡
- 活性区间条
- 表格结果渲染
- 批量结果视图
- 空结果 / 失败结果处理

### 7.4 `charts.js`

职责：

- 批量分布直方图
- 训练监控图表 option 构建
- 指标曲线更新
- 图表实例初始化与复用

要求：

- ECharts 相关逻辑集中，不再散落在训练流程里

### 7.5 `preflight.js`

职责：

- 训练前数据预检查状态
- preflight 弹窗
- launch summary 弹窗
- 训练启动前确认动作

### 7.6 `training_monitor.js`

职责：

- 底部抽屉控制
- 训练日志更新
- 训练状态轮询
- 训练结果摘要
- epoch / ETA / 指标同步

### 7.7 `main.js`

职责：

- 页面初始化
- tab 切换
- 文件选择显示
- 单分子 / 批量预测提交
- 训练启动入口
- 串联模型、结果、图表、训练监控模块

### 7.8 旧文件处理

保留：

- `src/web/static/js/activity_prediction_v2.legacy.backup.js`

模板改为顺序加载 `activity_prediction/` 目录下的新脚本文件。

## 8. 模板调整

### 8.1 `index.html`

当前：

- 直接加载 `/static/js/script.js`

调整后：

- 顺序加载 `/static/js/home/config.js`
- `/static/js/home/state.js`
- `/static/js/home/theme.js`
- `/static/js/home/formatters.js`
- `/static/js/home/molecule_renderer.js`
- `/static/js/home/chat_renderer.js`
- `/static/js/home/advanced_options.js`
- `/static/js/home/ws_client.js`
- `/static/js/home/main.js`

### 8.2 `activity_prediction.html`

当前：

- 直接加载 `/static/js/activity_prediction_v2.js`

调整后：

- 顺序加载 `/static/js/activity_prediction/utils.js`
- `/static/js/activity_prediction/model_manager.js`
- `/static/js/activity_prediction/results_renderer.js`
- `/static/js/activity_prediction/charts.js`
- `/static/js/activity_prediction/preflight.js`
- `/static/js/activity_prediction/training_monitor.js`
- `/static/js/activity_prediction/main.js`

## 9. 实施边界

本轮允许：

- 提取函数
- 重命名局部变量以提高清晰度
- 调整代码顺序
- 清除明显重复的工具函数
- 显式化跨模块依赖

本轮不做：

- UI 视觉调整
- 文案改写
- 新功能增加
- WebSocket 协议变更
- 后端接口变更
- 从普通脚本迁移到打包器或框架

## 10. 风险与控制

### 10.1 风险

1. 普通脚本顺序加载依赖较强，引用顺序错误会导致页面初始化失败。
2. 原始大文件中存在共享变量，拆分后若状态迁移不完整，可能出现行为偏差。
3. 聊天页与活性预测页都包含较多 DOM 选择器，拆分后容易出现空节点访问问题。

### 10.2 控制方式

1. 先保留旧文件备份。
2. 第一轮只做职责切分，不做功能改写。
3. 拆分过程中优先抽离纯函数和明显独立的 UI 模块。
4. 每完成一个页面的拆分，都执行一次脚本语法检查。
5. 模板改引用后，至少验证首页和活性预测页是否能初始化。

## 11. 验收标准

满足以下条件视为本轮整理完成：

1. `script.js` 不再作为运行入口，首页脚本改为目录化模块加载。
2. `activity_prediction_v2.js` 不再作为运行入口，活性预测脚本改为目录化模块加载。
3. 两个旧文件都保留备份。
4. 新模块命名与职责划分清晰，风格与现有 `design / docking / reverse_target` 对齐。
5. 首页可正常加载、发送消息、切换模型、打开高级选项。
6. 活性预测页可正常切换 tab、执行单分子预测、批量预测、训练监控初始化。
7. 所有新脚本通过基础语法检查。

## 12. 后续可继续做但不属于本轮范围的优化

- 将首页聊天模块进一步拆出独立 API 协议层
- 为活性预测训练监控补充结构化日志渲染层
- 把历史遗留的内联样式与内联 `onclick` 继续向模块内部收口
- 为拆分后的模块补充前端 smoke test
