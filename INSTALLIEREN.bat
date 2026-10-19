@echo off
rem ==========================================================================
rem  Trading-Software Gold und Bitcoin - INSTALLATION (Doppelklick)
rem  Richtet alles ein: Ordner, Python-Umgebung, Pakete, Desktop- und
rem  Startmenue-Symbole. Erneut ausfuehren = Aktualisierung, Daten bleiben.
rem  MetaTrader 5 muss bereits installiert sein. Es werden KEINE Orders gesendet.
rem ==========================================================================
title Trading - Installation
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\installieren.ps1"
echo.
pause
