@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not defined PYTHON set "PYTHON=python"
"%PYTHON%" -c "import sys; print(sys.version)" >nul 2>nul
if errorlevel 1 (
    echo 未找到 Python。请先安装 Python 3.11 或更高版本，或设置 PYTHON 环境变量。
    pause
    exit /b 1
)

echo [1/3] 安装打包依赖...
"%PYTHON%" -m pip install --disable-pip-version-check -r requirements-build.txt
if errorlevel 1 goto :failed

echo [2/3] 打包单文件版...
"%PYTHON%" -m PyInstaller --noconfirm --clean build.spec
if errorlevel 1 goto :failed

echo [3/3] 打包文件夹便携版（内含 Python 运行时）...
"%PYTHON%" -m PyInstaller --noconfirm --clean build-portable.spec
if errorlevel 1 goto :failed

echo.
echo 打包完成：
echo   dist\ModMigrator.exe                  单文件版
echo   dist\ModMigrator-Portable\            文件夹便携版（Python 运行时在 _internal）
pause
exit /b 0

:failed
echo.
echo 打包失败，请查看上方错误信息。
pause
exit /b 1
