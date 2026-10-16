@echo off
REM Sapphire smoke test for Windows: finds the sapphire conda env and runs tools\windows_smoke.py.
REM Stop Sapphire first (launcher Stop). Pass-through args: --install  --pythonw  --index cu128
setlocal
set "ROOT=%~dp0.."
set "PY="
for %%P in ("%USERPROFILE%\miniconda3\envs\sapphire\python.exe" "%USERPROFILE%\Miniconda3\envs\sapphire\python.exe" "%USERPROFILE%\anaconda3\envs\sapphire\python.exe" "%LOCALAPPDATA%\miniconda3\envs\sapphire\python.exe") do (
    if not defined PY if exist %%P set "PY=%%~P"
)
if not defined PY (
    echo Could not find the sapphire conda env. Open an Anaconda Prompt, "conda activate sapphire",
    echo then run:  python tools\windows_smoke.py %*
    pause
    exit /b 2
)
cd /d "%ROOT%"
echo Using %PY%
"%PY%" tools\windows_smoke.py %*
echo.
echo Exit code %ERRORLEVEL% ^(the number of failed checks^). Logs: user\logs\windows_smoke\
pause
