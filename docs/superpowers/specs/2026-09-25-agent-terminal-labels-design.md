# Agent 任务面板终态标签最小修复

用户已选择在本批实施最小展示修复。真实页面已复现：partial正文正确，但任务面板只有100%与泛称“Agent事件”；现有解析还将planning_completed/tool_completed误显示为整项已完成。

## 设计

- 仅五种任务终态决定最终进度文字：task_completed→已完成、task_partial→部分完成、task_failed→失败、task_rejected→已拒绝、task_cancelled→已取消。
- 终态文字优先于数值progress。非终态继续显示既有百分比或执行中；planning_completed、tool_completed不结束任务，也不显示整项已完成。
- 补齐partial/rejected/cancelled事件中文标签。保留现有事件字段优先级、终态集合、CSS分类、消息转义、事件追加和等待动画清理机制。
- 不改后端协议、工具结果、科学数值、模型配置、多轮面板复用结构，不增加状态机或框架。
- 首页main.js更新缓存版本；原有两个精确缓存版本断言同步更新，其他安全、顺序、行为断言保留。

## 文件边界

src/web/static/js/home/main.js、src/web/templates/index.html；tests/home_agent_task_panel_test.js、tests/home_workflow_completion_behavior_test.js、tests/home_scientific_references_test.js；本设计和集成验收文档。

## 验收

先以实际提取的JS函数补Node失败用例：五种终态（含progress=1）、planning/tool completed中间态、事件中文标签和缓存版本；再最小改映射。
运行五个首页Node回归、修改JS语法检查、真实页面partial复验及静态模板回归。保留RDKit输出及明确失败原因，不让100%掩盖partial。
独立审查后仅本地交付；本批不推送、合并或部署。
