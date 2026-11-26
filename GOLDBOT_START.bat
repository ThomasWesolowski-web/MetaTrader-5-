@echo off
rem ==========================================================================
rem  Gold-Bot (Order-Block-Strategie XAUUSD M15) - NUR Demokonto
rem  Einstellungen: goldbot.toml   Beschreibung: docs\goldbot.md
rem ==========================================================================
setlocal
cd /d "%~dp0"
title Trading - Gold-Bot
if not exist "tmp" mkdir "tmp"
set "TEMP=%~dp0tmp"
set "TMP=%~dp0tmp"
if exist ".venv\Scripts\python.exe" goto venv_ok
echo  Bitte zuerst INSTALLIEREN.bat bzw. START.bat ausfuehren (Python-Umgebung fehlt).
if not "%1"=="auto" pause
exit /b 1
:venv_ok
".venv\Scripts\python.exe" -c "import anthropic" >nul 2>&1
if not errorlevel 1 goto pkg_ok
echo  Paket anthropic (fuer die KI-Pruefung) wird installiert ...
".venv\Scripts\python.exe" -m pip install --no-cache-dir "anthropic>=1.0" >nul 2>&1
:pkg_ok
echo.
echo  Gold-Bot - handelt nur auf dem DEMOKONTO. Beenden: Strg+C oder STOP.bat
echo.
".venv\Scripts\python.exe" goldbot.py
if not "%1"=="auto" pause
