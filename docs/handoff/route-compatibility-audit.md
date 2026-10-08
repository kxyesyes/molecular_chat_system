# 路由兼容层审计

审计对象：活性预测、活性模型、分子工具和对接报告四组路由。

## 结论

正式应用注册不再把整个 `api_routes` 模块作为依赖容器传入。四组路由均通过
显式窄依赖接收运行资源；`_support` 只保留给旧的直接注册调用方，并通过
`src/web/routes/route_compat.py::lazy_dependency` 延迟解析，以保持旧调用方在
注册后替换 logger、上传读取器或执行器的行为。

| 路由模块 | 正式注册依赖 | `_support` 保留范围 |
| --- | --- | --- |
| `activity_prediction_routes` | 活性预算执行器、上传读取器、logger | 旧活性直接注册调用 |
| `activity_model_routes` | 上传读取器、临时文件模块、logger | 旧训练/模型直接注册调用 |
| `molecule_utility_routes` | 线程池执行器、logger | 旧分子工具直接注册调用 |
| `docking_report_routes` | 对接服务、报告图片验证器、logger | 旧报告直接注册调用 |

## 不应继续做的变更

- 不从路由模块直接导入或读取 `api_routes`。
- 不把 `_support` 删除后再用新的全局依赖容器替代。
- 不改变公开 URL、请求参数、响应结构、错误状态或旧直接调用方的动态替换语义。

## 验证边界

以下测试分别覆盖显式依赖优先级、旧动态兼容、注册契约和路由模块不反向
依赖 `api_routes`。兼容入口仍是有意保留的迁移边界，而不是正式生产装配路径。

```text
python -m pytest tests/test_api_route_boundary.py tests/test_route_compatibility.py \
  tests/test_runtime_boundary_contracts.py -q
```

如果未来要移除 `_support`，必须先单独完成调用方迁移、发布弃用周期和一轮
兼容回归；本架构整理不以删除公共兼容参数为目标。
