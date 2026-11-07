@echo off
setlocal
cd /d "%~dp0"
title StudyBench
echo.
echo  StudyBench - first start installs everything it needs. Please wait, this can take a few minutes.
echo.

rem --- Python
where py >nul 2>nul || where python >nul 2>nul
if errorlevel 1 (
  echo Python is not installed. Trying to install it with winget...
  winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
  echo Please close this window and double-click run.bat again.
  pause & exit /b
)
if not exist .venv ( py -3 -m venv .venv 2>nul || python -m venv .venv )
call .venv\Scripts\activate
python -m pip install -q --upgrade pip
pip install -q -r requirements.txt || ( echo Installing packages failed. Check your internet connection. & pause & exit /b )

rem --- Local AI (Ollama) - installed once if missing
where ollama >nul 2>nul
if errorlevel 1 if not exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" (
  echo Installing Ollama ^(local AI^)...
  winget install -e --id Ollama.Ollama --silent --accept-package-agreements --accept-source-agreements
)
rem --- OCR for scanned PDFs (optional)
if not exist "C:\Program Files\Tesseract-OCR\tesseract.exe" (
  winget install -e --id UB-Mannheim.TesseractOCR --silent --accept-package-agreements --accept-source-agreements >nul 2>nul
)

echo.
echo  Starting StudyBench. The AI model and the speech model download by themselves in the background
echo  ^(progress is shown in the app, bottom left^).
echo.
python app.py
pause
