@echo off
rem 开发者：幻方者（https://space.bilibili.com/3546815194467241）
chcp 65001 >nul
cd /d "%~dp0"

rem 从 main.py 里读出 APP_VERSION，产物文件名自动带版本号
set "APP_VER="
for /f "tokens=3" %%a in ('findstr /b /c:"APP_VERSION" main.py') do set "APP_VER=%%a"
set "APP_VER=%APP_VER:"=%"
if "%APP_VER%"=="" set "APP_VER=unknown"

echo [1/3] 清理旧的构建产物...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
rem 只删旧的 exe，保留 exe\.minecraft 里的游戏数据
if exist "exe\MCHLauncherForPy.exe" del /q "exe\MCHLauncherForPy.exe"
if exist "exe\MCHLauncherForPy_*.exe" del /q "exe\MCHLauncherForPy_*.exe"

echo [2/3] 开始打包（单文件 exe，版本 %APP_VER%）...
echo 提示：set MCHL_ONEDIR=1 后运行本脚本，可产出启动快约 0.4s 的文件夹版
echo 提示：set MCHL_FULL_DOTNET=1 可保留 pythonnet 的 CoreCLR 程序集（默认裁掉以缩小体积）
python -m PyInstaller MCHLauncherForPy.spec --noconfirm --clean --distpath exe --workpath build
if errorlevel 1 (
    echo 打包失败。
    pause
    exit /b 1
)

echo [3/3] 按版本号重命名...
if exist "exe\MCHLauncherForPy.exe" move /y "exe\MCHLauncherForPy.exe" "exe\MCHLauncherForPy_%APP_VER%.exe" >nul

echo.
echo 完成：exe\MCHLauncherForPy_%APP_VER%.exe
for %%F in ("exe\MCHLauncherForPy_%APP_VER%.exe") do echo 大小：%%~zF 字节
pause
