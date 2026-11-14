@echo off
chcp 65001 >nul
cd /d "%~dp0"

if exist "ModMigrator.exe" (
    start "" "ModMigrator.exe"
    exit /b 0
)
if exist "dist\ModMigrator.exe" (
    start "" "dist\ModMigrator.exe"
    exit /b 0
)

if not defined PYTHON set "PYTHON=pythonw"
"%PYTHON%" main.py
if errorlevel 1 (
    echo.
    echo 启动失败。请确认已安装 Python 3.11+ 和依赖：pip install -r requirements.txt
    pause
)
