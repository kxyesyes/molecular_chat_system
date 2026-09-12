# 家族活性结果前端集成

日期：2026-09-12。分支 `codex/family-activity-ui-integration`，基线 API 提交 `3261d7f`。
父批次 [PR #14](https://github.com/kxyesyes/molecular_chat_system/pull/14) CI 7/7 已通过，仍待具体合并授权。
本分支与 Agent 接线并行，但只修改活性页面及其测试，不修改 Agent/后端/权限。

实施总体计划 Task 3：显式目标选择、保留 legacy、partial 行展示、零值/null 区分、结果来源、安全渲染。
候选源为 `5432968` 的前端片段，不能覆盖 main 已有的安全契约。

状态：本地实现、独立 SPEC/QUALITY 与桌面浏览器契约冒烟通过；尚未发布/合并。
不把 UI 测试桩或临时合成结果当成真实科研预测，不启用生产模型，不重启用户服务。

## 文件和测试

- activity_prediction.html、activity_prediction/main.js、results_renderer.js。
- tests/activity_family_results_test.js：RED 17/18 失败 → GREEN 18/18。
- 全部 Node 脚本 9/9，通过 changed JS 语法与 diff 检查。
- 独立 SPEC APPROVED：9 个 Node 脚本、47 个 JS 语法及跨格式/不可用组补充探针通过。
- 独立 QUALITY APPROVED：18 项正式测试、4 项额外对抗测试、3 个 JS 语法及 diff 检查通过。

## 实际浏览器契约冒烟

在仅绑定 loopback 的临时 FastAPI fixture 提供实际模板/静态文件及明确标注的合成响应，
不导入 MedChat app，不连接模型，不启动训练或真实 sandbox。依次通过浏览器完成：

1. 单分子 PDE 请求，partial 仍显示分类概率 0.0%，缺少 pIC50 显示不可用。
2. 展开 provenance，恶意 img/svg 字符串为纯文本；结果 DOM 内新增 img/svg/script 为 0。
3. 修改表单到 BuChE 后，旧结果仍显示来自 PDE，不被当前选择重标记。
4. 上传合成两行文件，保留 CCO partial 与无效结构 failed 的原顺序，失败行不补数值。
5. 选择 legacy 再提交，切换到任务感知列并保留预测值 0.0000。

独立的 fixture-observations 调试地址被浏览器客户端拦截，未绕过；以上结果来自实际表单和可见 DOM，
并非该调试地址的日志断言。移动端断点/目标服务器和真实模型端到端仍未验证。
测试后关闭浏览器页并停止本任务的临时服务器，未动生产端口/服务。
fixture 脚本与合成文件位于忽略的 scratch/，不提交。
