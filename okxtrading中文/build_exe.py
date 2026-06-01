#!/usr/bin/env python3
"""
OKX交易信号分析系统 - 打包脚本
用于将项目打包成可执行程序
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path

def main():
    # 项目根目录
    project_dir = Path(__file__).parent
    print(f"项目目录: {project_dir}")
    
    # 确保data目录存在
    data_dir = project_dir / "data"
    data_dir.mkdir(exist_ok=True)
    
    # 检查主程序文件
    main_script = project_dir / "okxtrading.py"
    if not main_script.exists():
        print(f"错误: 找不到主程序文件 {main_script}")
        return 1
    
    # 检查依赖
    print("检查依赖安装情况...")
    try:
        import pandas, numpy, requests, pywebview
        print("✓ 核心依赖已安装")
    except ImportError as e:
        print(f"✗ 缺少依赖: {e}")
        print("请先运行: pip install -r requirements.txt")
        return 1
    
    # 创建dist目录
    dist_dir = project_dir / "dist"
    if dist_dir.exists():
        print("清理旧的dist目录...")
        shutil.rmtree(dist_dir)
    dist_dir.mkdir(exist_ok=True)
    
    # PyInstaller打包命令
    print("开始打包为可执行程序...")
    
    # 构建PyInstaller命令
    cmd = [
        "pyinstaller",
        "--onefile",  # 单文件exe
        "--windowed",  # 无控制台窗口（GUI程序）
        "--name=OKX交易信号分析系统",  # 程序名称
        "--icon=NONE",  # 无图标
        "--add-data=data;data",  # 包含data目录
        "--hidden-import=pandas",  # 显式隐藏导入
        "--hidden-import=numpy",
        "--hidden-import=requests",
        "--hidden-import=pywebview",
        "--hidden-import=matplotlib",
        "--hidden-import=plotly",
        "--hidden-import=http.server",
        "--hidden-import=urllib.parse",
        "--hidden-import=urllib.request",
        "--hidden-import=webbrowser",
        "--hidden-import=threading",
        "--hidden-import=sqlite3",
        "--hidden-import=json",
        "--hidden-import=logging",
        "--hidden-import=argparse",
        "--hidden-import=os",
        "--hidden-import=sys",
        "--hidden-import=pathlib",
        "--hidden-import=datetime",
        "--hidden-import=time",
        "--hidden-import=reportlab",
        "--hidden-import=reportlab.graphics.charts",
        "--hidden-import=reportlab.graphics.shapes",
        "--hidden-import=Crypto",
        "--hidden-import=Crypto.Cipher",
        "--hidden-import=Crypto.Cipher.AES",
        "--hidden-import=license_manager",
        "--clean",  # 清理临时文件
        str(main_script)
    ]
    
    print(f"执行命令: {' '.join(cmd)}")
    
    try:
        # 执行打包
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        print("✓ 打包成功")
        print(result.stdout)
        
        # 移动生成的文件到dist目录
        exe_src = project_dir / "dist" / "OKX交易信号分析系统.exe"
        exe_dst = dist_dir / "OKX交易信号分析系统.exe"
        
        if exe_src.exists():
            shutil.move(str(exe_src), str(exe_dst))
            print(f"✓ EXE文件已生成: {exe_dst}")
        else:
            # 尝试其他可能的路径
            for possible_path in project_dir.glob("**/*.exe"):
                if "OKX交易信号分析系统" in str(possible_path):
                    shutil.move(str(possible_path), str(exe_dst))
                    print(f"✓ EXE文件已生成: {exe_dst}")
                    break
        
        # 创建启动脚本
        create_launcher_scripts(project_dir, dist_dir)
        
        # 创建使用说明
        create_readme(project_dir, dist_dir)
        
        print("\n" + "="*60)
        print("打包完成！")
        print(f"可执行程序: {dist_dir / 'OKX交易信号分析系统.exe'}")
        print(f"启动脚本: {dist_dir / '启动程序.bat'}")
        print(f"使用说明: {dist_dir / '使用说明.txt'}")
        print("="*60)
        
        return 0
        
    except subprocess.CalledProcessError as e:
        print(f"✗ 打包失败: {e}")
        print(f"错误输出: {e.stderr}")
        return 1
    except Exception as e:
        print(f"✗ 打包过程中出现错误: {e}")
        return 1

def create_launcher_scripts(project_dir, dist_dir):
    """创建启动脚本"""
    
    # Windows批处理文件
    bat_content = """@echo off
chcp 65001 >nul
echo OKX交易信号分析系统 - 启动器
echo ========================================
echo.

REM 检查程序是否存在
if not exist "OKX交易信号分析系统.exe" (
    echo 错误: 找不到主程序文件 "OKX交易信号分析系统.exe"
    echo 请确保此批处理文件与主程序在同一目录下
    pause
    exit /b 1
)

echo 正在启动OKX交易信号分析系统...
echo 首次运行可能需要较长时间加载依赖...
echo 请耐心等待...
echo.

REM 运行程序
"OKX交易信号分析系统.exe"

if %errorlevel% neq 0 (
    echo.
    echo 程序运行出错，错误码: %errorlevel%
    echo 请检查:
    echo 1. 是否安装了必要的依赖
    echo 2. 网络连接是否正常
    echo 3. 是否有足够的磁盘空间
) else (
    echo.
    echo 程序已正常退出
)

echo.
pause
"""
    
    bat_path = dist_dir / "启动程序.bat"
    with open(bat_path, "w", encoding="utf-8") as f:
        f.write(bat_content)
    
    # PowerShell脚本
    ps_content = """# OKX交易信号分析系统 - PowerShell启动器
Write-Host "OKX交易信号分析系统 - 启动器" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 检查程序是否存在
$exePath = "OKX交易信号分析系统.exe"
if (-not (Test-Path $exePath)) {
    Write-Host "错误: 找不到主程序文件 '$exePath'" -ForegroundColor Red
    Write-Host "请确保此脚本与主程序在同一目录下" -ForegroundColor Yellow
    Read-Host "按回车键退出"
    exit 1
}

Write-Host "正在启动OKX交易信号分析系统..." -ForegroundColor Green
Write-Host "首次运行可能需要较长时间加载依赖..." -ForegroundColor Yellow
Write-Host "请耐心等待..." -ForegroundColor Yellow
Write-Host ""

# 运行程序
try {
    & .\$exePath
    $exitCode = $LASTEXITCODE
} catch {
    Write-Host "程序运行出错: $_" -ForegroundColor Red
    $exitCode = 1
}

if ($exitCode -ne 0) {
    Write-Host ""
    Write-Host "程序运行出错，错误码: $exitCode" -ForegroundColor Red
    Write-Host "请检查:" -ForegroundColor Yellow
    Write-Host "1. 是否安装了必要的依赖" -ForegroundColor Yellow
    Write-Host "2. 网络连接是否正常" -ForegroundColor Yellow
    Write-Host "3. 是否有足够的磁盘空间" -ForegroundColor Yellow
} else {
    Write-Host ""
    Write-Host "程序已正常退出" -ForegroundColor Green
}

Write-Host ""
Read-Host "按回车键退出"
"""
    
    ps_path = dist_dir / "启动程序.ps1"
    with open(ps_path, "w", encoding="utf-8") as f:
        f.write(ps_content)

def create_readme(project_dir, dist_dir):
    """创建使用说明"""
    
    readme_content = """OKX交易信号分析系统 - 使用说明
================================

一、程序说明
------------
这是一个专业的OKX交易所交易信号分析工具，提供：
1. 实时K线数据分析
2. 趋势判断（EMA分析）
3. 支撑阻力位识别
4. 大单检测
5. 交易信号生成
6. 可视化图表展示

二、系统要求
------------
- Windows 7/8/10/11 64位系统
- 至少4GB内存
- 稳定的网络连接（需要访问OKX API）
- 至少500MB可用磁盘空间

三、使用方法
------------
1. 双击运行"启动程序.bat"或"OKX交易信号分析系统.exe"
2. 首次运行会自动创建本地数据库缓存
3. 程序启动后会自动打开浏览器窗口显示分析结果
4. 支持以下命令行参数：
   - 指定品种：--instruments BTC ETH XAU
   - 强制刷新：--refresh（忽略本地缓存，从API重新获取数据）
   - 指定数据库路径：--db-path ./data/my.db

四、目录结构
------------
程序目录包含以下重要文件：
- OKX交易信号分析系统.exe     # 主程序
- 启动程序.bat                # Windows启动脚本
- 启动程序.ps1               # PowerShell启动脚本
- data/                      # 数据缓存目录（自动创建）
  - trading.db              # SQLite数据库文件

五、注意事项
------------
1. 首次运行需要下载依赖，可能需要较长时间（1-2分钟）
2. 程序需要网络连接访问OKX API
3. 分析结果仅供参考，不构成投资建议
4. data目录下的数据库文件可以删除以清空缓存
5. 如果程序无法启动，请确保系统已安装Microsoft Visual C++ Redistributable

六、故障排除
------------
1. 程序无法启动：
   - 检查是否被杀毒软件拦截
   - 尝试以管理员身份运行
   - 检查系统是否安装VC++运行库

2. 网络连接失败：
   - 检查网络连接
   - 确认可以访问 https://www.okx.com
   - 尝试关闭防火墙或代理

3. 程序运行缓慢：
   - 首次运行需要构建缓存
   - 确保有足够的磁盘空间
   - 关闭其他占用资源的程序

七、技术支持
------------
如有问题，请检查程序输出的错误信息，或联系开发者。

================================
免责声明：本程序仅供学习和研究使用，不构成任何投资建议。
使用本程序产生的任何投资风险由用户自行承担。
"""
    
    readme_path = dist_dir / "使用说明.txt"
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(readme_content)

if __name__ == "__main__":
    sys.exit(main())