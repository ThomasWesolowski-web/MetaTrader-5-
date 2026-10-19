@echo off
rem ==========================================================================
rem  Trading-Software - DEINSTALLATION. Daten bleiben erhalten, ausser die
rem  Loeschung wird ausdruecklich bestaetigt. MT5 und Python bleiben unveraendert.
rem ==========================================================================
start "Trading - Deinstallation" powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\deinstallieren.ps1"
exit
