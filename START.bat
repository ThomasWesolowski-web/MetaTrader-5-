@echo off
rem ==========================================================================
rem  Trading-Software Gold und Bitcoin - START (Doppelklick genuegt)
rem  Nur Beobachtung (Paper-Trading) und Dashboard. Es werden KEINE Orders
rem  gesendet, kein Demo- und kein Echtgeldhandel.
rem ==========================================================================
setlocal
cd /d "%~dp0"
title Trading - Start
echo.
echo  Trading-Software Gold und Bitcoin
echo  Nur Beobachtung - es werden KEINE Orders gesendet.
echo.

rem --- 1. Python-Umgebung (nur beim ersten Start oder nach einem Umzug) ------
if exist ".venv\Scripts\python.exe" goto venv_ok
echo  Erster Start: Python-Umgebung wird eingerichtet ...
rem Python 3.13/3.12/3.11 bevorzugt - dafuer gibt es das Paket MetaTrader5
for %%V in (3.13 3.12 3.11 3) do if not exist ".venv\Scripts\python.exe" py -%%V -m venv .venv >nul 2>&1
if not exist ".venv\Scripts\python.exe" python -m venv .venv
if exist ".venv\Scripts\python.exe" goto venv_ok
echo.
echo  FEHLER: Python wurde nicht gefunden. Bitte Python 3.11 oder neuer installieren
echo  und beim Installieren "Add python.exe to PATH" anhaken.
pause
exit /b 1
:venv_ok

rem --- 2. Pakete (nur wenn etwas fehlt) ------------------------------------
".venv\Scripts\python.exe" -c "import streamlit, MetaTrader5, pandas, pyarrow" >nul 2>&1
if not errorlevel 1 goto packages_ok
echo  Pakete werden installiert, das dauert einige Minuten ...
".venv\Scripts\python.exe" -m pip install --no-cache-dir -r requirements.txt
if not errorlevel 1 goto packages_ok
echo.
echo  FEHLER bei der Installation. Bitte INSTALLIEREN.bat erneut ausfuehren -
echo  es waehlt eine passende Python-Version und nennt den Grund.
pause
exit /b 1
:packages_ok

rem --- 3. Beobachtung: zuerst Kursdaten aktualisieren, dann beobachten ------
tasklist /V /FI "WINDOWTITLE eq Trading - Beobachtung*" | find /I "cmd.exe" >nul
if not errorlevel 1 (
    echo  Beobachtung laeuft bereits.
) else (
    echo  Beobachtung wird gestartet - eigenes Fenster, minimiert.
    start "Trading - Beobachtung" /min cmd /k ".venv\Scripts\python.exe daten_aktualisieren.py & .venv\Scripts\python.exe beobachtung_ausfuehren.py"
)

rem --- 4. Dashboard ----------------------------------------------------------
netstat -ano | find ":8501 " | find "LISTENING" >nul
if not errorlevel 1 (
    echo  Dashboard laeuft bereits.
) else (
    echo  Dashboard wird gestartet - eigenes Fenster, minimiert.
    start "Trading - Dashboard" /min cmd /k ".venv\Scripts\python.exe -m streamlit run dashboard.py"
    timeout /t 10 /nobreak >nul
)

rem --- 5. Browser oeffnen ----------------------------------------------------
start "" http://localhost:8501
echo.
echo  Fertig. Das Dashboard oeffnet sich im Browser: http://localhost:8501
echo  MetaTrader 5 muss geoeffnet und eingeloggt sein. Ist es noch nicht bereit,
echo  wartet die Beobachtung und versucht es jede Minute erneut.
echo  Beenden: STOP.bat doppelklicken.
echo.
timeout /t 15
