@echo off
rem ==========================================================================
rem  Einmalig ausfuehren: Desktop-Symbole "Trading starten" und "Trading stoppen"
rem  anlegen und auf Wunsch den automatischen Start mit Windows einrichten.
rem ==========================================================================
setlocal
cd /d "%~dp0"
set "DIR=%~dp0"
echo.
echo  Desktop-Symbole werden angelegt ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$w=New-Object -ComObject WScript.Shell; $d=[Environment]::GetFolderPath('Desktop'); foreach($p in @(@('Trading starten','START.bat',137),@('Trading stoppen','STOP.bat',131))){ $l=$w.CreateShortcut((Join-Path $d ($p[0]+'.lnk'))); $l.TargetPath=(Join-Path $env:DIR $p[1]); $l.WorkingDirectory=$env:DIR; $l.IconLocation=('shell32.dll,'+$p[2]); $l.Save() }"
if errorlevel 1 (
    echo  FEHLER beim Anlegen der Symbole.
) else (
    echo  Auf dem Desktop: "Trading starten" und "Trading stoppen".
)
echo.
choice /C JN /M " Soll das Programm automatisch mit Windows starten"
if errorlevel 2 goto no_autostart
powershell -NoProfile -ExecutionPolicy Bypass -Command "$w=New-Object -ComObject WScript.Shell; $s=[Environment]::GetFolderPath('Startup'); $l=$w.CreateShortcut((Join-Path $s 'Trading starten.lnk')); $l.TargetPath=(Join-Path $env:DIR 'START.bat'); $l.WorkingDirectory=$env:DIR; $l.WindowStyle=7; $l.Save()"
echo  Autostart eingerichtet. Entfernen: Win+R, shell:startup, "Trading starten" loeschen.
goto end
:no_autostart
echo  Kein Autostart eingerichtet.
:end
echo.
pause
