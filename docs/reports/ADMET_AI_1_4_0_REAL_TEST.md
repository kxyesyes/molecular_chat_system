# ADMET-AI 1.4.0 真实权重验收报告

日期：2026-10-04（Asia/Shanghai）

## 范围

本报告只记录真实本地 ADMET-AI 1.4.0 权重的脱敏验收，不记录 API key、完整环境变量、模型文件路径或模型文件本身。MedChat 主进程使用现有科学计算环境；ADMET-AI 使用独立 Python 3.10 worker，避免替换现有 RG-MPNN/Torch 栈。

## 执行方式

```powershell
$env:ADMET_AI_PYTHON = "<venv-dir>\admet-ai-140\Scripts\python.exe"
$env:PYTHONPATH = "<repo-root>"
python scripts/verify_admet_ai_real.py --repeat 3
```

实际运行环境：Python 3.10.20 的 MedChat 环境调用独立 worker；worker 中固定 `admet-ai==1.4.0`，模型运行在 CPU。测试输入为两个合法 SMILES，分别使用 `real-001`、`real-002` 作为 molecule ID。

## 结果

| 指标 | 实际结果 |
|---|---:|
| 重复次数 | 3 |
| 成功次数 | 3/3 |
| 每次成功行数 | 2/2 |
| 每行端点数 | 49 |
| prediction method | `admet_ai` |
| model version | `1.4.0` |
| 权重标识 | `sha256:84060276c0886dd8019262ac029b483b1f00d6600c5ac2f2218b74c478051608` |
| worker 初始化耗时 | 5871.90 ms |
| 预测耗时 | 92.04 ms、83.22 ms、83.20 ms |
| 预测耗时 p50 | 83.22 ms |
| 预测耗时 p95（3 次样本的线性插值） | 91.16 ms |
| ID 顺序 | 每次均为 `real-001`, `real-002` |
| API key | 未用于本地 ADMET 推理；脚本仅输出存在性布尔值 |

## 验收结论

- 真实权重加载成功，3 次批量推理均返回 `admet_ai`，不是 demo、fallback 或 RDKit 规则替代值。
- 结果行数、ID 顺序和 49 个端点在重复运行中稳定。
- 每个端点携带单位、任务类型和来源；物化端点与模型端点在结构化结果中分开标记。
- 生成候选的 ID 在 `molecule_batch` 绑定中保留，并由 ADMET 结果与排序器继续消费；相同 SMILES 的不同候选不会因去重而错配。
- 本报告不代表模型准确率、外部实验一致性或临床有效性已经验证；需要独立基准数据集才能做准确性评估。
- worker 初始化约 6 秒，因此生产部署应保持进程和模型缓存常驻，不应每个请求创建新 worker。

## 失败与边界测试

以下测试在 MedChat 主环境中通过：模型不可用时显式失败、超时不返回科学数值、批量部分失败保留逐项错误、无效输入不以规则值补齐、候选 ID 经过绑定后进入 ADMET 结果并可被排序器消费。若 `ADMET_AI_PYTHON` 或固定版本依赖缺失，验收应为不可用/失败，而不是成功。
