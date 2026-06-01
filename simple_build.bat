@echo off
chcp 65001 >nul
echo OKX Trading Signal Analysis System - Simple Build Script
echo ========================================
echo.

REM Check PyInstaller
where pyinstaller >nul 2>&1
if %errorlevel% neq 0 (
    echo Error: pyinstaller not found. Please install it first: pip install pyinstaller
    pause
    exit /b 1
)

echo Building executable...
echo This may take a few minutes, please wait...
echo.

REM Create spec file
echo Creating spec file...
pyinstaller --onefile --windowed --name="OKXTradingSignal" --add-data="data;data" okxtrading.py --specpath build

if %errorlevel% neq 0 (
    echo Build failed!
    pause
    exit /b 1
)

echo.
echo Build successful!
echo.
echo Generated files:
echo   - dist\OKXTradingSignal.exe
echo   - build\  (temporary files, can be deleted)
echo.
echo Run dist\OKXTradingSignal.exe to start the program
echo.
pause
