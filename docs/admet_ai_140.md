# ADMET-AI 1.4.0 部署与追溯说明

MedChat 的 `admet_predictor` 现在只把真实 ADMET-AI 1.4.0 本地权重的输出作为模型预测。RDKit 物化计算、模型端点预测和排序启发式在结果中分开标记；模型不可用、输入无效、逐项失败和超时不会被填成规则值或模拟值。

## 为什么使用独立 worker

ADMET-AI 1.4.0 的官方包固定 `torch==2.5.0`。MedChat 的 RG-MPNN/部署环境有自己的 Torch/CUDA 约束，直接在同一个环境升级 Torch 可能破坏既有活性预测。因此推荐使用独立 Python 3.10 worker，让主 Agent 通过 JSON-lines 调用；模型会在 worker 进程内加载一次并缓存。

## 安装

在仓库外创建独立环境，不要把模型权重复制到 Git 仓库：

```powershell
python -m venv <venv-dir>\admet-ai-140
<venv-dir>\admet-ai-140\Scripts\python.exe -m pip install -r requirements-admet-ai-140.txt
```

运行时只设置 worker 解释器路径：

```powershell
$env:ADMET_AI_PYTHON = "<venv-dir>\admet-ai-140\Scripts\python.exe"
python main.py
```

`ADMET_AI_PYTHON` 是运行时配置，不写入 `.env`、日志或验收报告。生产调用必须显式配置该 Python 3.10 worker；缺少配置、版本不符或 worker 无法启动时，工具会返回明确的不可用状态，不会改用主进程或规则值。

worker 请求受单批 100 个分子和调用超时约束；超时会终止并回收 worker。应用 shutdown/reload 会清理缓存 worker。ADMET-AI 的可选分子缓存关闭，避免在长生命周期服务中无界增长。

## 输出契约

成功的每个分子行包含：

- `molecule_id`、原始 `smiles`、`canonical_smiles`；
- ADMET-AI 端点值、端点单位、任务类型、类别和来源；
- `model_name=ADMET-AI`、`model_version=1.4.0`、权重 SHA-256 标识；
- `demo_mode=false`、`fallback_used=false`、warnings 和工具级 provenance。

百分位参考值与端点值分开保存。`risk_count` 只用于候选排序的显式筛选启发式，不是毒性或安全结论。模型预测不等于实验结果。

## 验证

使用项目的 Python 3.10 环境运行契约测试：

```powershell
python -m pytest tests\agent\test_admet_ai_integration.py -q -p no:cacheprovider
```

使用独立 worker 做真实权重冒烟测试：

```powershell
$env:ADMET_AI_PYTHON = "<venv-dir>\admet-ai-140\Scripts\python.exe"
$env:PYTHONPATH = "<repo-root>"
python -c "from src.agent.tools.admet_predictor import ADMETPredictor; print(ADMETPredictor().execute('SMILES: CCO')['success'])"
```

真实权重测试只证明模型能加载并产生有限输出，不等于模型准确率、外部数据集表现或实验可重复性验证。模型权重、缓存、日志和输出文件均不得提交。
