@echo off
setlocal

REM --- Sapphire launcher (runs in this window) ---
set SAPPHIRE_DIR=%~dp0
set SAPPHIRE_DIR=%SAPPHIRE_DIR:~0,-1%

REM Find conda
set CONDA=%USERPROFILE%\miniconda3\condabin\conda.bat
if not exist "%CONDA%" set CONDA=%USERPROFILE%\Miniconda3\condabin\conda.bat
if not exist "%CONDA%" set CONDA=%USERPROFILE%\anaconda3\condabin\conda.bat

if not exist "%CONDA%" (
  echo Conda not found. Install Miniconda or add conda to PATH.
  pause
  exit /b 1
)

call "%CONDA%" activate sapphire
if errorlevel 1 (
  echo Failed to activate conda env 'sapphire'. Run: conda create -n sapphire python=3.11
  pause
  exit /b 1
)
cd /d "%SAPPHIRE_DIR%"
python main.py
pause

endlocal
