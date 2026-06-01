@echo off
chcp 65001 >nul
echo OKX交易信号分析系统 - 简化打包脚本
echo ========================================
echo.

REM 检查PyInstaller
where pyinstaller >nul 2>&1
if %errorlevel% neq 0 (
    echo 错误: 未找到pyinstaller，请先安装: pip install pyinstaller
    pause
    exit /b 1
)

echo 正在打包为可执行程序...
echo 这可能需要几分钟时间，请耐心等待...
echo.

REM 创建spec文件
echo 创建spec文件...
pyinstaller --onefile --windowed --name="OKX交易信号分析系统" --add-data="data;data" trading_signal.py --specpath build

if %errorlevel% neq 0 (
    echo 打包失败！
    pause
    exit /b 1
)

echo.
echo ✓ 打包成功！
echo.
echo 生成的文件：
echo   - dist\OKX交易信号分析系统.exe
echo   - build\  （临时文件，可删除）
echo.
echo 请运行 dist\OKX交易信号分析系统.exe 启动程序
echo.
pause