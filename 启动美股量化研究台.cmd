@echo off
rem 便携启动器：优先已打包版本，其次使用仓库管理的虚拟环境。
setlocal
set "PROJECT_ROOT=%~dp0"
cd /d "%PROJECT_ROOT%"

rem 优先启动已打包的最新版本（releases\<version>\USQuantResearch\）
set "PACKAGED_APP="
for /f "delims=" %%D in ('dir /b /ad /o-d "%PROJECT_ROOT%releases" 2^>nul') do (
    if not defined PACKAGED_APP (
        if exist "%PROJECT_ROOT%releases\%%D\USQuantResearch\USQuantResearch.exe" (
            set "PACKAGED_APP=%PROJECT_ROOT%releases\%%D\USQuantResearch\USQuantResearch.exe"
        )
    )
)
if defined PACKAGED_APP (
    start "" "%PACKAGED_APP%"
    exit /b 0
)

rem 源码启动只使用仓库自己的虚拟环境，避免随机落到缺依赖的系统 Python。
set "PYTHON_EXE="
if exist "%PROJECT_ROOT%.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%PROJECT_ROOT%.venv\Scripts\python.exe"
) else if exist "%PROJECT_ROOT%.venv313\Scripts\python.exe" (
    rem 兼容旧开发环境；新安装统一使用 .venv。
    set "PYTHON_EXE=%PROJECT_ROOT%.venv313\Scripts\python.exe"
)

if not defined PYTHON_EXE (
    echo.
    echo No managed Python environment was found.
    echo Run this once from PowerShell:
    echo   powershell -ExecutionPolicy Bypass -File scripts\bootstrap_windows.ps1
    echo.
    pause
    exit /b 2
)

"%PYTHON_EXE%" -c "import sys; raise SystemExit(0 if sys.version_info ^>= (3, 12) else 2)"
if errorlevel 1 (
    echo.
    echo Python 3.12 or newer is required. Recreate the environment with:
    echo   powershell -ExecutionPolicy Bypass -File scripts\bootstrap_windows.ps1
    pause
    exit /b 2
)

"%PYTHON_EXE%" -c "import PySide6"
if errorlevel 1 (
    echo.
    echo Desktop dependencies are missing. Repair the environment with:
    echo   powershell -ExecutionPolicy Bypass -File scripts\bootstrap_windows.ps1
    pause
    exit /b 3
)

"%PYTHON_EXE%" "%PROJECT_ROOT%desktop_main.py"
if errorlevel 1 (
    echo.
    echo Launch failed. Run scripts\verify.ps1 and keep this window open for the error text.
    pause
)
endlocal
