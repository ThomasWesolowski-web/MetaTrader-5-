@echo off
rem ==========================================================================
rem  Strategie-Finder - sehr viele Strategien fuer Gold (XAUUSD) durchrechnen
rem  und je Setup die beste heraussuchen (Walk-Forward, echte Kosten).
rem  NUR RECHNEN: es werden KEINE Orders gesendet (weder Demo noch Live).
rem  Beschreibung: docs\strategiefinder.md
rem ==========================================================================
setlocal
cd /d "%~dp0"
title Trading - Strategie-Finder
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
echo  STRATEGIE-FINDER fuer Gold (XAUUSD) - nur Rechnung, es wird NICHTS gehandelt.
echo  Ergebnis: Text hier im Fenster und ein Bericht im Browser (Ordner reports\strategiefinder).
echo.
echo    1  Alles durchrechnen (alle Strategien, M5 bis H4, mit Gold-Bot)   ca. 3-10 Minuten
echo    2  Schnell (ohne Gold-Bot)                                         ca. 1-3 Minuten
echo    3  Alles durchrechnen und den Sieger als Demo-Kandidat eintragen
echo       (nur wenn er ALLE Pruefungen besteht, nur Demokonto)
echo    0  Beenden
echo.
choice /c 1230 /n /m "  Auswahl (Taste 1-3 oder 0): "
set "WAHL=%errorlevel%"
if "%WAHL%"=="4" exit /b 0
echo.
set "KONTO=357"
set /p "KONTO=  Kontostand in USD [Enter = 357]: "
echo.
if "%WAHL%"=="1" ".venv\Scripts\python.exe" strategiefinder.py --konto %KONTO%
if "%WAHL%"=="2" ".venv\Scripts\python.exe" strategiefinder.py --schnell --konto %KONTO%
if "%WAHL%"=="3" ".venv\Scripts\python.exe" strategiefinder.py --demo-eintragen --konto %KONTO%
echo.
echo  Fertig. Eine Taste druecken fuer das Menue (Fenster schliessen = beenden).
pause >nul
goto menu
