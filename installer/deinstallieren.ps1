# Deinstallation der Trading-Software (Aufruf über DEINSTALLIEREN.bat bzw. Startmenü "Trading deinstallieren").
# Entfernt Symbole und Programmdateien. Kursdaten, Berichte und Beobachtungsprotokoll bleiben erhalten, außer
# ihre Löschung wird ausdrücklich bestätigt. MetaTrader 5 und Python werden nicht verändert.

$ErrorActionPreference = "Continue"
$Ziel = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $env:TEMP          # der Programmordner darf nicht das aktuelle Verzeichnis sein

Write-Host ""
Write-Host "  Trading-Software deinstallieren: $Ziel" -ForegroundColor White
$a = Read-Host "  Wirklich deinstallieren? [J/N]"
if (-not "$a".Trim().ToUpper().StartsWith("J")) { Write-Host "  Abgebrochen."; Start-Sleep 3; exit 0 }

$paper = Join-Path $Ziel "data\paper"
if (Test-Path $paper) { New-Item -ItemType File -Force -Path (Join-Path $paper "stop.anfrage") | Out-Null }
Start-Sleep -Seconds 4
& cmd /c 'taskkill /FI "WINDOWTITLE eq Trading - Beobachtung*" /T /F >nul 2>&1'
& cmd /c 'taskkill /FI "WINDOWTITLE eq Trading - Dashboard*" /T /F >nul 2>&1'

$desktop = [Environment]::GetFolderPath("Desktop")
foreach ($f in @((Join-Path $desktop "Trading starten.lnk"), (Join-Path $desktop "Trading stoppen.lnk"),
                 (Join-Path ([Environment]::GetFolderPath("Startup")) "Trading starten.lnk"))) {
    if (Test-Path $f) { Remove-Item $f -Force }
}
$menue = Join-Path ([Environment]::GetFolderPath("Programs")) "Trading"
if (Test-Path $menue) { Remove-Item $menue -Recurse -Force }
Write-Host "  Symbole entfernt."

$b = Read-Host "  Auch Kursdaten, Berichte und Beobachtungsprotokoll (Ordner data und reports) LÖSCHEN? [J/N] (Enter = N)"
$alles = "$b".Trim().ToUpper().StartsWith("J")
Get-ChildItem -Path $Ziel -Force | Where-Object { $alles -or ($_.Name -notin @("data", "reports")) } |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
if ($alles) {
    Remove-Item $Ziel -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "  Programm und Daten gelöscht."
} else {
    Write-Host "  Programm entfernt. Daten liegen weiter in $Ziel\data und $Ziel\reports."
}
Write-Host "  MetaTrader 5 und Python wurden nicht verändert."
Read-Host "  Enter zum Schließen"
