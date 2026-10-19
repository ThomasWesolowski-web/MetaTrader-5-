# Installation der Trading-Software Gold & Bitcoin (Beobachtung und Dashboard - es werden KEINE Orders gesendet).
# Aufruf über INSTALLIEREN.bat (Doppelklick). Läuft mit Windows PowerShell 5.1, ohne Administratorrechte.
#
# Ablauf: Python prüfen (bei Bedarf über winget installieren) -> MT5 prüfen -> Zielordner wählen -> laufende
# Beobachtung beenden -> Programmdateien kopieren -> vorhandene Daten übernehmen -> Python-Umgebung und Pakete ->
# Desktop- und Startmenü-Symbole, optional Autostart -> optional sofort starten.
# Ein erneuter Aufruf (z. B. mit einer neuen Version) aktualisiert die Installation; Daten bleiben erhalten.

param([string]$Ziel = "")

# "Continue": Windows PowerShell 5.1 wertet sonst jede Fehlerausgabe externer Programme (pip, py) als Abbruch.
# Wichtige Schritte prüfen ihren Erfolg selbst bzw. nutzen -ErrorAction Stop.
$ErrorActionPreference = "Continue"
$Quelle = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$AlterOrdner = Join-Path $env:SystemDrive "Trading\MetaTrader-5--master"
$Standardziel = Join-Path $env:SystemDrive "Trading\TradingBot"

function Titel($text) { Write-Host ""; Write-Host "== $text ==" -ForegroundColor Cyan }
function Ok($text) { Write-Host "   OK: $text" -ForegroundColor Green }
function Warnung($text) { Write-Host "   ACHTUNG: $text" -ForegroundColor Yellow }
function Abbruch($text) { Write-Host ""; Write-Host "   FEHLER: $text" -ForegroundColor Red; exit 1 }
function JaNein($frage, $standard = "J") {
    $a = Read-Host "   $frage [J/N] (Enter = $standard)"
    if ([string]::IsNullOrWhiteSpace($a)) { $a = $standard }
    return ($a.Trim().ToUpper().StartsWith("J") -or $a.Trim().ToUpper().StartsWith("Y"))
}
function FreiGB($pfad) {
    $root = [System.IO.Path]::GetPathRoot([System.IO.Path]::GetFullPath($pfad))
    $d = New-Object System.IO.DriveInfo($root)
    return [math]::Round($d.AvailableFreeSpace / 1GB, 1)
}

function Finde-Python {
    $kandidaten = @(@("py", "-3"), @("python"))
    $lokal = Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA "Programs\Python") -Filter python.exe -Recurse -Depth 1 -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending
    foreach ($p in $lokal) { $kandidaten += , @($p.FullName) }
    foreach ($k in $kandidaten) {
        try {
            $argumente = @()
            if ($k.Count -gt 1) { $argumente = $k[1..($k.Count - 1)] }
            $v = & $k[0] @argumente -c "import sys; print(sys.version_info[0], sys.version_info[1], sys.maxsize > 2**32)" 2>&1 |
                Where-Object { $_ -is [string] } | Select-Object -Last 1
            if ($LASTEXITCODE -eq 0 -and $v) {
                $teile = "$v".Trim().Split(" ")
                if ([int]$teile[0] -eq 3 -and [int]$teile[1] -ge 11 -and $teile[2] -eq "True") { return , $k }
            }
        } catch { }
    }
    return $null
}

Write-Host ""
Write-Host "  Trading-Software Gold & Bitcoin - Installation" -ForegroundColor White
Write-Host "  Beobachtung (Paper-Trading) und Dashboard. Es werden KEINE Orders gesendet."
Write-Host "  Kein Demo- und kein Echtgeldhandel."

# ------------------------------------------------------------------ 1. Python
Titel "1/7 Python"
$py = Finde-Python
if ($null -eq $py) {
    Warnung "Python 3.11 oder neuer (64 Bit) wurde nicht gefunden."
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget -and (JaNein "Python 3.13 jetzt automatisch installieren (winget, ca. 100 MB)?")) {
        & winget install -e --id Python.Python.3.13 --scope user --accept-package-agreements --accept-source-agreements
        $py = Finde-Python
    }
    if ($null -eq $py) {
        Abbruch ("Bitte Python von https://www.python.org/downloads/ installieren (Haken bei 'Add python.exe to PATH') " +
                 "und INSTALLIEREN.bat erneut starten.")
    }
}
Ok ("Python gefunden: " + ($py -join " "))

# ------------------------------------------------------------------ 2. MetaTrader 5
Titel "2/7 MetaTrader 5"
$suchorte = @($env:ProgramFiles, ${env:ProgramFiles(x86)}, (Join-Path $env:APPDATA "MetaQuotes")) | Where-Object { $_ -and (Test-Path $_) }
$mt5 = Get-ChildItem -Path $suchorte -Filter terminal64.exe -Recurse -Depth 2 -ErrorAction SilentlyContinue | Select-Object -First 1
if ($mt5) {
    Ok "MetaTrader 5 gefunden: $($mt5.FullName)"
} else {
    Warnung "MetaTrader 5 (terminal64.exe) wurde nicht gefunden. Es muss installiert und eingeloggt sein."
    if (-not (JaNein "Trotzdem fortfahren?" "N")) { Abbruch "Bitte zuerst MetaTrader 5 installieren." }
}

# ------------------------------------------------------------------ 3. Zielordner
Titel "3/7 Installationsordner"
Write-Host "   Freier Speicher:"
Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Free -gt 0 } |
    ForEach-Object { Write-Host ("     {0}:  {1} GB frei" -f $_.Name, [math]::Round($_.Free / 1GB, 1)) }
if ([string]::IsNullOrWhiteSpace($Ziel)) {
    $Ziel = Read-Host "   Ordner für die Installation (Enter = $Standardziel)"
    if ([string]::IsNullOrWhiteSpace($Ziel)) { $Ziel = $Standardziel }
}
$Ziel = [System.IO.Path]::GetFullPath($Ziel.Trim().Trim('"'))
try { New-Item -ItemType Directory -Force -Path $Ziel -ErrorAction Stop | Out-Null }
catch { Abbruch "Ordner $Ziel kann nicht angelegt werden: $($_.Exception.Message)" }
$frei = FreiGB $Ziel
if ($frei -lt 1.5) { Warnung "Auf diesem Laufwerk sind nur $frei GB frei (empfohlen: mindestens 1,5 GB)." }
Ok "Ziel: $Ziel ($frei GB frei)"

# ------------------------------------------------------------------ 4. Laufende Beobachtung beenden
Titel "4/7 Laufende Programme beenden"
foreach ($ordner in @($Ziel, $AlterOrdner) | Select-Object -Unique) {
    $paper = Join-Path $ordner "data\paper"
    if (Test-Path $paper) { New-Item -ItemType File -Force -Path (Join-Path $paper "stop.anfrage") | Out-Null }
}
Start-Sleep -Seconds 3
& cmd /c 'taskkill /FI "WINDOWTITLE eq Trading - Beobachtung*" /T /F >nul 2>&1'
& cmd /c 'taskkill /FI "WINDOWTITLE eq Trading - Dashboard*" /T /F >nul 2>&1'
Ok "keine Beobachtung und kein Dashboard mehr aktiv"

# ------------------------------------------------------------------ 5. Programmdateien und Daten
Titel "5/7 Programmdateien kopieren"
if ($Quelle.TrimEnd("\") -ieq $Ziel.TrimEnd("\")) {
    Ok "Installation im Download-Ordner selbst - nichts zu kopieren"
} else {
    $cfg = Join-Path $Ziel "config.toml"
    if (Test-Path $cfg) { Copy-Item $cfg (Join-Path $Ziel "config.toml.bak") -Force }
    & robocopy $Quelle $Ziel /E /XD .venv data reports __pycache__ .pytest_cache .git /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { Abbruch "Kopieren fehlgeschlagen (robocopy-Code $LASTEXITCODE)." }
    Ok "Programmdateien in $Ziel (vorherige config.toml gesichert als config.toml.bak)"
}
Get-ChildItem -Path $Ziel -Recurse -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue

$zielDaten = Join-Path $Ziel "data"
if (-not (Test-Path (Join-Path $zielDaten "market")) -and ($AlterOrdner.TrimEnd("\") -ine $Ziel.TrimEnd("\")) -and
    (Test-Path (Join-Path $AlterOrdner "data\market"))) {
    Write-Host "   In der bisherigen Installation $AlterOrdner liegen Kursdaten, Berichte und der Beobachtungsstand."
    if (JaNein "Diese Daten in die neue Installation übernehmen (verschieben, empfohlen)?") {
        foreach ($teil in @("data", "reports")) {
            $von = Join-Path $AlterOrdner $teil
            $nach = Join-Path $Ziel $teil
            if (-not (Test-Path $von)) { continue }
            $gleichesLaufwerk = ([System.IO.Path]::GetPathRoot($von) -ieq [System.IO.Path]::GetPathRoot($nach))
            if ($gleichesLaufwerk -and -not (Test-Path $nach)) {
                try { Move-Item -Path $von -Destination $nach -ErrorAction Stop }   # sofort, ohne zusätzlichen Platz
                catch { Abbruch "Verschieben von $teil fehlgeschlagen: $($_.Exception.Message)" }
            } else {
                & robocopy $von $nach /E /MOVE /NFL /NDL /NJH /NJS /NP | Out-Null
                if ($LASTEXITCODE -ge 8) { Abbruch "Verschieben von $teil fehlgeschlagen (robocopy-Code $LASTEXITCODE)." }
            }
        }
        Ok "Daten übernommen. Der alte Ordner $AlterOrdner wird nicht mehr gebraucht und kann später gelöscht werden."
    }
}

# ------------------------------------------------------------------ 6. Python-Umgebung und Pakete
Titel "6/7 Python-Umgebung und Pakete (beim ersten Mal einige Minuten)"
$venvPy = Join-Path $Ziel ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    $argumente = @()
    if ($py.Count -gt 1) { $argumente = $py[1..($py.Count - 1)] }
    & $py[0] @argumente -m venv (Join-Path $Ziel ".venv")
    if (-not (Test-Path $venvPy)) { Abbruch "Python-Umgebung konnte nicht angelegt werden." }
}
& $venvPy -m pip install --no-cache-dir --disable-pip-version-check -q -r (Join-Path $Ziel "requirements.txt")
if ($LASTEXITCODE -ne 0) { Abbruch "Paketinstallation fehlgeschlagen - oft ist die Festplatte voll. Speicher freigeben und erneut starten." }
& $venvPy -c "import MetaTrader5, streamlit, pandas, pyarrow" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Abbruch "Pakete sind nicht vollständig installiert." }
Ok "Python-Umgebung bereit"

# ------------------------------------------------------------------ 7. Symbole
Titel "7/7 Desktop- und Startmenü-Symbole"
$shell = New-Object -ComObject WScript.Shell
function Verknuepfung($datei, $programm, $icon, $stil = 1) {
    $l = $shell.CreateShortcut($datei)
    $l.TargetPath = $programm
    $l.WorkingDirectory = $script:Ziel
    $l.IconLocation = $icon
    $l.WindowStyle = $stil
    $l.Save()
}
$desktop = [Environment]::GetFolderPath("Desktop")
$menue = Join-Path ([Environment]::GetFolderPath("Programs")) "Trading"
New-Item -ItemType Directory -Force -Path $menue | Out-Null
foreach ($ort in @($desktop, $menue)) {
    Verknuepfung (Join-Path $ort "Trading starten.lnk") (Join-Path $Ziel "START.bat") "shell32.dll,137"
    Verknuepfung (Join-Path $ort "Trading stoppen.lnk") (Join-Path $Ziel "STOP.bat") "shell32.dll,131"
}
Set-Content -Path (Join-Path $menue "Trading Dashboard.url") -Value "[InternetShortcut]`r`nURL=http://localhost:8501/" -Encoding ASCII
Verknuepfung (Join-Path $menue "Trading Ordner.lnk") $Ziel "shell32.dll,3"
Verknuepfung (Join-Path $menue "Trading deinstallieren.lnk") (Join-Path $Ziel "DEINSTALLIEREN.bat") "shell32.dll,31"
Ok "Desktop: 'Trading starten' und 'Trading stoppen'; Startmenü: Ordner 'Trading'"

$autostart = Join-Path ([Environment]::GetFolderPath("Startup")) "Trading starten.lnk"
if (JaNein "Soll das Programm automatisch mit Windows starten?" "N") {
    Verknuepfung $autostart (Join-Path $Ziel "START.bat") "shell32.dll,137" 7
    Ok "Autostart eingerichtet (MT5 muss das Passwort gespeichert haben, damit es sich selbst einloggt)"
} elseif (Test-Path $autostart) {
    Remove-Item $autostart -Force
    Ok "Autostart entfernt"
}

Write-Host ""
Write-Host "  Installation abgeschlossen: $Ziel" -ForegroundColor Green
Write-Host "  Starten: Desktop-Symbol 'Trading starten'. Beenden: 'Trading stoppen'."
Write-Host "  Es werden keine Orders gesendet. Risikogrenzen unverändert."
if (JaNein "Jetzt starten?") { Start-Process -FilePath (Join-Path $Ziel "START.bat") -WorkingDirectory $Ziel }
exit 0
