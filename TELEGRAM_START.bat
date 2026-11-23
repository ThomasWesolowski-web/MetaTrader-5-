@echo off
rem ==========================================================================
rem  Telegram-Signale automatisch handeln (Doppelklick genuegt)
rem  Einstellungen: telegram.toml   Zugang: telegram_zugang.toml
rem  Beschreibung:  docs\telegram_signale.md
rem ==========================================================================
setlocal
cd /d "%~dp0"
title Trading - Telegram-Signale
if not exist "tmp" mkdir "tmp"
set "TEMP=%~dp0tmp"
set "TMP=%~dp0tmp"
if exist ".venv\Scripts\python.exe" goto venv_ok
echo  Bitte zuerst INSTALLIEREN.bat bzw. START.bat ausfuehren (Python-Umgebung fehlt).
pause
exit /b 1
:venv_ok
".venv\Scripts\python.exe" -c "import telethon" >nul 2>&1
if not errorlevel 1 goto pkg_ok
echo  Paket telethon wird installiert ...
".venv\Scripts\python.exe" -m pip install --no-cache-dir "telethon>=1.36"
:pkg_ok
if exist "telegram_zugang.toml" goto zugang_ok
echo.
echo  Es fehlt noch die Datei telegram_zugang.toml mit deinem Telegram-Zugang.
echo  1. https://my.telegram.org oeffnen, mit deiner Telefonnummer anmelden
echo  2. "API development tools" - App anlegen (Name egal)
echo  3. api_id und api_hash unten eingeben
echo.
set /p TG_ID= api_id: 
set /p TG_HASH= api_hash: 
> "telegram_zugang.toml" echo api_id = %TG_ID%
>> "telegram_zugang.toml" echo api_hash = "%TG_HASH%"
echo  Gespeichert in telegram_zugang.toml (bleibt nur auf diesem PC).
:zugang_ok
echo.
echo  Beim ersten Start fragt Telegram nach deiner Telefonnummer (+49...) und
echo  dem Code, den du in der Telegram-App bekommst. Danach nie wieder.
echo  Beenden: Strg+C oder STOP.bat
echo.
".venv\Scripts\python.exe" telegram_signale.py
pause
