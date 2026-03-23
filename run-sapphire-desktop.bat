@echo off
setlocal EnableDelayedExpansion

REM --- Sapphire desktop launcher (opens in new window + browser) ---
set "SAPPHIRE_DIR=%~dp0"
set "SAPPHIRE_DIR=%SAPPHIRE_DIR:~0,-1%"

REM Find conda
set "CONDA=%USERPROFILE%\miniconda3\condabin\conda.bat"
if not exist "%CONDA%" set "CONDA=%USERPROFILE%\Miniconda3\condabin\conda.bat"
if not exist "%CONDA%" set "CONDA=%USERPROFILE%\anaconda3\condabin\conda.bat"

if not exist "%CONDA%" (
  echo Conda not found. Install Miniconda or add conda to PATH.
  pause
  exit /b 1
)

REM Write a temp launcher to avoid nested-quote issues with start/cmd /k
set "LAUNCHER=%TEMP%\sapphire_launch.bat"
(
  echo @echo off
  echo call "!CONDA!" activate sapphire
  echo if errorlevel 1 (
  echo   echo Failed to activate conda env 'sapphire'. Run: conda create -n sapphire python=3.11
  echo   pause
  echo   exit /b 1
  echo ^)
  echo cd /d "!SAPPHIRE_DIR!"
  echo python main.py
  echo pause
) > "!LAUNCHER!"

REM Start Sapphire in a new window via the temp script
start "Sapphire Server" cmd /k "!LAUNCHER!"

REM Give the server a moment to start, then open the UI
timeout /t 3 /nobreak >nul
start "" https://localhost:8073

endlocal
