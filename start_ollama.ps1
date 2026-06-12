# Ollama服务启动脚本 - 针对4GB显存优化
# 用法: .\start_ollama.ps1

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Ollama服务启动脚本 (4GB显存优化配置)" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# 设置环境变量
Write-Host "[1/3] 配置环境变量..." -ForegroundColor Yellow
$env:OLLAMA_CONTEXT_LENGTH = "2048"
$env:OLLAMA_NUM_PARALLEL = "1"
$env:OLLAMA_GPU_OVERHEAD = "0"

Write-Host "  ✓ OLLAMA_CONTEXT_LENGTH = 2048 (降低显存占用)" -ForegroundColor Green
Write-Host "  ✓ OLLAMA_NUM_PARALLEL = 1 (单任务模式)" -ForegroundColor Green
Write-Host "  ✓ OLLAMA_GPU_OVERHEAD = 0 (最小化GPU开销)" -ForegroundColor Green
Write-Host ""

# 检查Ollama是否已安装
Write-Host "[2/3] 检查Ollama安装..." -ForegroundColor Yellow
try {
    $ollamaVersion = ollama --version 2>$null
    Write-Host "  ✓ Ollama已安装: $ollamaVersion" -ForegroundColor Green
} catch {
    Write-Host "  ✗ 错误: 未找到Ollama，请先安装 https://ollama.ai" -ForegroundColor Red
    exit 1
}
Write-Host ""

# 启动Ollama服务
Write-Host "[3/3] 启动Ollama服务..." -ForegroundColor Yellow
Write-Host "  监听端口: 11434" -ForegroundColor Cyan
Write-Host "  上下文长度: 2048 tokens" -ForegroundColor Cyan
Write-Host "  当前模型: gmm-llama:latest" -ForegroundColor Cyan
Write-Host ""
Write-Host "按 Ctrl+C 停止服务" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# 启动服务
ollama serve
