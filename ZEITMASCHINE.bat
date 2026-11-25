@echo off
rem ==========================================================================
rem  Zeitmaschine - Strategien auf den eigenen MT5-Kursen testen
rem  NUR RECHNEN: es werden KEINE Orders gesendet (weder Demo noch Live).
rem  Beschreibung: docs\zeitmaschine.md
rem ==========================================================================
setlocal
cd /d "%~dp0"
title Trading - Zeitmaschine
if not exist "tmp" mkdir "tmp"
set "TEMP=%~dp0tmp"
set "TMP=%~dp0tmp"
if exist ".venv\Scripts\python.exe" goto venv_ok
echo  Bitte zuerst INSTALLIEREN.bat bzw. START.bat ausfuehren (Python-Umgebung fehlt).
pause
exit /b 1
:venv_ok
:menu
cls
echo.
echo  ZEITMASCHINE - nur Rechnung auf vergangenen Kursen, es wird NICHTS gehandelt.
echo  Ergebnis: Text hier im Fenster und ein Bericht im Browser (Ordner reports\zeitmaschine).
echo.
echo    1  Gold-Bot (Order Blocks)        XAUUSD M15, letztes Jahr
echo    2  Opening Range Breakout Gold    XAUUSD M5,  letztes Jahr
echo    3  Opening Range Breakout Bitcoin BTCUSD M5,  letztes Jahr
echo    4  Opening Range Breakout Euro    EURUSD M5,  London (Kerzen aus dem offenen MT5)
echo    5  Alles nacheinander (1 bis 4)
echo    6  Gold-Bot, ganze Historie (dauert einige Minuten)
echo    0  Beenden
echo.
choice /c 1234560 /n /m "  Auswahl (Taste 1-6 oder 0): "
set "WAHL=%errorlevel%"
if "%WAHL%"=="7" exit /b 0
echo.
set "KONTO=357"
set /p "KONTO=  Kontostand in USD [Enter = 357]: "
echo.
if "%WAHL%"=="1" ".venv\Scripts\python.exe" zeitmaschine.py goldbot --konto %KONTO%
if "%WAHL%"=="2" ".venv\Scripts\python.exe" zeitmaschine.py orb --symbol XAUUSD --konto %KONTO%
if "%WAHL%"=="3" ".venv\Scripts\python.exe" zeitmaschine.py orb --symbol BTCUSD --konto %KONTO%
if "%WAHL%"=="4" ".venv\Scripts\python.exe" zeitmaschine.py orb --symbol EURUSD --konto %KONTO%
if "%WAHL%"=="5" ".venv\Scripts\python.exe" zeitmaschine.py alle --konto %KONTO%
if "%WAHL%"=="6" ".venv\Scripts\python.exe" zeitmaschine.py goldbot --alles --konto %KONTO%
echo.
echo  Fertig. Eine Taste druecken fuer das Menue (Fenster schliessen = beenden).
pause >nul
goto menu
