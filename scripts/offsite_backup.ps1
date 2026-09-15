# ENFAL — Offsite-Kopie der SQLite-Backups vom Railway-Volume auf diesen PC.
#
# Warum: Die App sichert alle 6 h nach /data/backups — aber auf DEMSELBEN
# Volume. Railway-Volume-Backups sind im aktuellen Plan nicht verfuegbar
# (Plan-Limit maxBackupsCount=0). Ohne diese Kopie wuerde ein Verlust des
# Volumes oder des Railway-Projekts alle Nutzerdaten zerstoeren.
#
# Ablauf: neuestes auto_ki_backup_*.db aus /backups laden -> in eine Temp-Datei
# -> PRAGMA integrity_check -> erst dann ins Zielverzeichnis -> die letzten
# $Behalten Kopien bleiben. Bereits vorhandene Dateien werden nicht erneut geladen.
#
# Voraussetzungen (einmalig): Railway-CLI angemeldet (`railway login`),
# SSH-Schluessel bei Railway registriert (`railway ssh keys add`), Python 3.
# Enthaelt KEINE Secrets. Die Backups enthalten personenbezogene Daten —
# Zielordner nicht oeffentlich teilen.
#
# Aufruf:  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\offsite_backup.ps1
# Exitcode 0 = ok (neu geladen oder schon aktuell), 1 = Fehler (siehe Log).
param(
    [string]$Ziel = (Join-Path $env:USERPROFILE "ENFAL-Backups"),
    [int]$Behalten = 30,
    [string]$Projekt = "4212013e-0b3b-47f5-95a9-9ec3b8095f80",
    [string]$Umgebung = "production",
    [string]$Service = "auto-ki-backend",
    [string]$Volume = "auto-ki-backend-volume"
)
$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path $Ziel | Out-Null
$Log = Join-Path $Ziel "offsite_backup.log"
# Railway-CLI schreibt Statuszeilen nach stderr; PowerShell 5.1 wuerde das mit
# ErrorActionPreference=Stop als Fehler werten. Deshalb eigener Aufruf mit
# Pruefung des Exitcodes.
$RailwayExe = $null
function Invoke-RailwayCli([string[]]$argumente) {
    $alt = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { $out = & $RailwayExe @argumente 2>$null; $code = $LASTEXITCODE } finally { $ErrorActionPreference = $alt }
    if ($code -ne 0) { throw "railway $($argumente[5..($argumente.Length-1)] -join ' ') -> Exitcode $code" }
    return $out
}
function Find-Programm([string]$name, [string]$fest) {
    if ($fest -and (Test-Path -LiteralPath $fest)) { return $fest }
    foreach ($k in @(Get-Command $name -CommandType Application -ErrorAction SilentlyContinue)) {
        $pfad = $k.Source.Trim().Trim([char]34)
        if ($pfad -and (Test-Path -LiteralPath $pfad)) { return $pfad }
    }
    throw "$name nicht gefunden."
}
function Log([string]$m) {
    $z = "{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $m
    Add-Content -Path $Log -Value $z -Encoding utf8
    Write-Output $z
}

try {
    # Innerhalb von try: auch ein fehlendes CLI (z. B. andere PATH-Umgebung
    # der geplanten Aufgabe) landet im Log statt still mit Exitcode 1 zu enden.
    # Feste Pfade zuerst: in der Umgebung der geplanten Aufgabe findet
    # Get-Command die Programme nicht zuverlaessig. Das Railway-CLI liegt als
    # eigenstaendige railway.exe unter %USERPROFILE%\.enfal\bin (Kopie aus dem
    # npm-Paket @railway/cli), sonst ueber den PATH.
    $RailwayExe = Find-Programm "railway" (Join-Path $env:USERPROFILE ".enfal\bin\railway.exe")
    $PythonExe = Find-Programm "python" (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe")
    $basis = @("volume", "-p", $Projekt, "-e", $Umgebung, "-s", $Service, "files", "--volume", $Volume)
    $roh = Invoke-RailwayCli ($basis + @("list", "/backups", "--json"))
    $json = ($roh | Out-String)
    $json = $json.Substring($json.IndexOf("{"))
    $dateien = (ConvertFrom-Json $json).files |
        Where-Object { $_.type -eq "file" -and $_.name -like "auto_ki_backup_*.db" } |
        Sort-Object modifiedAt -Descending
    if (-not $dateien) { throw "Keine Backups unter /backups gefunden." }
    $neu = $dateien[0]
    $zielDatei = Join-Path $Ziel $neu.name

    if ((Test-Path $zielDatei) -and ((Get-Item $zielDatei).Length -eq [int64]$neu.size)) {
        Log "OK bereits vorhanden: $($neu.name)"
    } else {
        $tmp = Join-Path $Ziel ("_laden_" + $neu.name)
        if (Test-Path $tmp) { Remove-Item $tmp -Force }
        Invoke-RailwayCli ($basis + @("download", $neu.path, $tmp, "--overwrite")) | Out-Null
        if (-not (Test-Path $tmp)) { throw "Download fehlgeschlagen: $($neu.path)" }
        $groesse = (Get-Item $tmp).Length
        if ($groesse -ne [int64]$neu.size) { throw "Groesse weicht ab ($groesse statt $($neu.size))." }
        $pruef = & $PythonExe -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); print(c.execute('PRAGMA integrity_check').fetchone()[0]); print(c.execute('SELECT COUNT(*) FROM users').fetchone()[0])" $tmp
        if ($pruef[0] -ne "ok") { Remove-Item $tmp -Force; throw "integrity_check: $($pruef[0])" }
        Move-Item $tmp $zielDatei -Force
        Log ("OK geladen: {0} ({1:N0} KB, integrity=ok, Konten={2})" -f $neu.name, ($groesse / 1KB), $pruef[1])
    }

    $alle = Get-ChildItem $Ziel -Filter "auto_ki_backup_*.db" | Sort-Object Name -Descending
    $alle | Select-Object -Skip $Behalten | ForEach-Object { Remove-Item $_.FullName -Force; Log "Rotation: $($_.Name) entfernt" }
    exit 0
} catch {
    Log ("FEHLER: {0} (Zeile {1}: {2})" -f $_.Exception.Message, $_.InvocationInfo.ScriptLineNumber, $_.InvocationInfo.Line.Trim())
    exit 1
}
