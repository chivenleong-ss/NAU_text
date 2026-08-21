@echo off
chcp 65001 >nul
echo ================================================================================
echo 中建集团结算审核智能体 - 启动脚本
echo ================================================================================
echo.

echo [1/3] 检查Python环境...
python --version
if %errorlevel% neq 0 (
    echo [错误] 未检测到Python环境，请先安装Python 3.8+
    pause
    exit /b 1
)
echo.

echo [2/3] 检查依赖包...
python -c "import duckdb, pandas, flask, flask_cors" 2>nul
if %errorlevel% neq 0 (
    echo [提示] 检测到缺失依赖，开始安装...
    echo.
    pip install --no-cache-dir duckdb pandas flask flask-cors -i https://pypi.tuna.tsinghua.edu.cn/simple
    if %errorlevel% neq 0 (
        echo.
        echo [错误] 依赖安装失败，可能原因：
        echo   1. 磁盘空间不足（需要至少50MB）
        echo   2. 网络连接问题
        echo.
        echo 解决方案：
        echo   方案1：清理磁盘空间后重新运行
        echo   方案2：手动执行命令：pip cache purge
        echo   方案3：使用国内镜像源安装
        echo.
        pause
        exit /b 1
    )
)
echo [✓] 依赖检查完成
echo.

echo [3/3] 启动Web服务...
echo.
echo ================================================================================
echo 服务地址: http://localhost:5100
echo 停止服务: 按 Ctrl+C
echo ================================================================================
echo.

python app.py

pause
