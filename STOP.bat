@echo off
rem ==========================================================================
rem  Trading-Software - STOP: Beobachtung sauber beenden, Dashboard schliessen
rem ==========================================================================
setlocal
cd /d "%~dp0"
title Trading - Stop
echo.
echo  Beobachtung wird beendet (der Stand bleibt gespeichert) ...
if not exist "data\paper" mkdir "data\paper"
type nul > "data\paper\stop.anfrage"
timeout /t 5 /nobreak >nul
taskkill /FI "WINDOWTITLE eq Trading - Beobachtung*" /T /F >nul 2>&1
echo  Dashboard wird beendet ...
taskkill /FI "WINDOWTITLE eq Trading - Dashboard*" /T /F >nul 2>&1
if exist "data\paper\stop.anfrage" del "data\paper\stop.anfrage"
echo.
echo  Alles beendet. MetaTrader 5 bleibt geoeffnet.
echo.
timeout /t 8
