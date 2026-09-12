# 家族预测共享服务与 API 集成交接

日期：2026-09-12。分支 `codex/family-activity-api-integration`；基线 main `1a1677b`（PR #13）。
状态：本批实现与两轮独立审查通过；扩展回归发现未稳定复现的既有沙盒测试失败，详见下文。尚未合并本批。

## 目标与范围

执行历史活性集成计划 Task 1，仅涉及共享预测服务和两个 HTTP 预测端点。
源参考为 `codex/activity-integration-blockers` / `5432968`，选择性移植，不覆盖整个 api_routes。
完整目标仍包括 Agent/UI、训练编排、决策协议及部署准备，不能把本批通过视为全部完成。
用户明确要求先完成代码集成，服务器参数暂不阻塞代码工作。

## 当前文件

- `src/activity/prediction_service.py`
- `src/web/routes/api_routes.py`：仅 activity predict/batch_predict 段。
- `tests/test_activity_family_api.py`
- `tests/test_activity_family_inference_integration.py`：保留原 Python 前向与安全断言，增加两个 API 入口。
- `docs/activity_family_api.md`、`docs/activity_family_inference.md` 链接、本交接、latest 索引。
- 总体计划 `docs/superpowers/plans/2026-09-12-historical-integration-completion.md` 与历史残差清单。

## 恢复与 TDD 证据

上轮实施与清单审查任务因额度异常终止，不能记为独立审查通过。此次重新读取实际文件，
保留已有测试和生产改动，没有回退混杂工作树。

聚焦命令（MedChat Conda Python）：

```text
python -B -m pytest tests/test_activity_family_api.py tests/test_activity_family_inference_integration.py tests/test_activity_prediction_contract.py -q -p no:cacheprovider --tb=short
```

- 恢复后实测 RED：4 failed、59 passed。warnings 为 None/整数时异常，字符串被拆字符、字典键被错误汇总。
- 参照家族预测器的列表警告契约，仅过滤汇总阶段非列表容器，不修改原行或丢失 partial 观察。
- GREEN：63 passed、2 既有警告，11.69 秒；不是未执行的计划结果。
- 上轮无模块/API 的初始 RED 输出不在本次可核实结果中，不编造计数。
- 完整活性回归：1142 passed、5 skipped、2 warnings，166.11 秒。跳过均为 Windows 符号链接权限/平台条件。
- Agent 与反幻觉/平台健康回归：1688 passed、1 skipped、7 warnings，24.46 秒。
- contract 验收状态 passed；compileall、8 个既有 Node 契约脚本、git diff --check 通过。
- 独立规格审查 APPROVED，另跑 112 passed；独立质量审查 APPROVED，另跑 63 passed 与 20 项补充检查。
- 审查仅覆盖本批，不代表后续 Agent/UI/决策集成已获批准。CI 尚待发布后验证。

### 扩展回归的非绿色结果

`python -B -m pytest tests --ignore=tests/agent -q -rs -p no:cacheprovider --tb=short`：
5752 passed、1 failed、237 skipped、6 warnings、171 subtests passed，440.88 秒。
该命令包含 sandbox_broker/task_runtime，不只是 CI 的 root 分片。

失败项为 `tests/sandbox_broker/test_service.py::test_manifest_survives_service_reopen_and_tamper_and_cross_job_fail_closed`。
对这次测试专用临时 SQLite 只读检查发现状态 `failed / artifact_failed`，cleanup succeeded、零登记产物；
因此 get_manifest 正确拒绝，不是返回虚假对接产物。当前批次与 main 的 sandbox 源码/测试无差异。
单项立即重跑 1 passed（0.78 秒），完整 test_service.py 重跑 172 passed（30.71 秒）。
尚未确定全套运行下的触发因素，不能把重跑通过说成根因已修复，也不能宣称全仓回归全绿。
将其保留为独立稳定性待查项；本批不放宽产物校验或混入沙盒修改。
237 跳过主要为 Windows/POSIX、权限及显式 opt-in 的宿主/外部服务条件，非真实部署验收。

## 保护边界

原始项目 `f377443` 混杂状态保持；没有读取真实 CSV/密钥/权重，不重启服务，不启用模型。
不改变 registry/model-card/family predictor 的强校验、默认模型选择、上传上限或管理权限。
测试只使用临时合成数据与未训练权重；不得作为真实科研性能依据。
main 同步曾出现 TLS 网络失败；本轮重试 fetch 成功，确认 origin/main 与基线相同。
未关闭 TLS 校验或改写远端。
