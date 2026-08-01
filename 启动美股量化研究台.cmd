@echo off
rem 便携启动器：不写入任何本机绝对路径，可直接随仓库分发。
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

rem 否则用本地虚拟环境（存在时）或系统 Python 直接运行源码
if exist "%PROJECT_ROOT%.venv313\Scripts\python.exe" (
    "%PROJECT_ROOT%.venv313\Scripts\python.exe" "%PROJECT_ROOT%desktop_main.py"
) else (
    python "%PROJECT_ROOT%desktop_main.py"
)
if errorlevel 1 (
    echo.
    echo Launch failed. Keep this window open and send me the error text.
    pause
)
endlocal
