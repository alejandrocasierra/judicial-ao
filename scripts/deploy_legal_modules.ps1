#Requires -Version 5.1
<#
.SYNOPSIS
  Despliegue controlado de los módulos legales (corpus + migraciones + derivados).

.DESCRIPTION
  Por defecto es DRY-RUN: imprime los comandos y no modifica nada. Para ejecutar
  hay que pasar -Execute. En producción exige -AuthorizeProduction y la cadena
  exacta -ConfirmProduction "DEPLOY-PRODUCTION", además de un backup verificado.

  Secuencia:
    1. Preflight (target, env, docker, revisión actual)
    2. Backup pg_dump -Fc + verificación pg_restore -l
    3. Migración Alembic (0054 -> 0064 por defecto; -ToHead para el head actual)
    4. Importación opcional del corpus (-ImportCorpus)
    5. Backfill de hechos
    6. Reindex de chunks estructurados
    7. Rebuild del grafo
    8. Smoke test (health + conteos)
    9. Rollback explícito (-RollbackTo / -RestoreBackup) si algo falla

.PARAMETER Target
  local-test | staging | production. Identifica el target; no se inventa.

.PARAMETER EnvFile
  Archivo de entorno del target (por defecto .env).

.PARAMETER ComposeFile
  Compose base (por defecto docker-compose.yml).

.PARAMETER ProdOverlay
  Añade docker-compose.prod.yml (solo targets con proxy/TLS).

.PARAMETER Execute
  Sin este switch el script solo simula.

.PARAMETER TargetRevision
  Revisión destino de Alembic. Default: 0064_legal_hold_release (head actual).

.PARAMETER ToHead
  Usa `head` en vez de TargetRevision. Puede incluir migraciones P1 posteriores.

.PARAMETER ImportCorpus
  Ejecuta scripts/import_legal_rules.py --all con data/legal_rules/co.

.PARAMETER CaseId
  Limita backfill/reindex/grafo a un caso. Si se omite, usa --all.

.PARAMETER ReindexLocalHash
  Fuerza EMBEDDING_PROVIDER=local-hash (solo test/local; nunca producción).

.PARAMETER RollbackTo
  Revisión Alembic a la que volver si falla un paso.

.PARAMETER RestoreBackup
  Si falla y hay backup verificado, restaura el dump. Operación destructiva.

.EXAMPLE
  ./scripts/deploy_legal_modules.ps1 -Target local-test
  ./scripts/deploy_legal_modules.ps1 -Target local-test -Execute -CaseId 38865959-...
  ./scripts/deploy_legal_modules.ps1 -Target production -Execute `
      -AuthorizeProduction -ConfirmProduction "DEPLOY-PRODUCTION" -ToHead
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet("local-test", "staging", "production")]
  [string]$Target,

  [string]$EnvFile = ".env",
  [string]$ComposeFile = "docker-compose.yml",
  [switch]$ProdOverlay,

  [switch]$Execute,
  [switch]$AuthorizeProduction,
  [string]$ConfirmProduction = "",

  [string]$TargetRevision = "0064_legal_hold_release",
  [switch]$ToHead,

  [switch]$ImportCorpus,
  [string]$CaseId = "",
  [switch]$ReindexLocalHash,

  [string]$RollbackTo = "",
  [switch]$RestoreBackup,

  [string]$BackupDir = "var/backups/legal-modules",
  [string]$SmokeBaseUrl = "http://127.0.0.1:8000/health"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$LogDir = Join-Path $Root "var/logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir "deploy-legal-modules-$Timestamp.log"

function Write-Log {
  param([string]$Message, [string]$Level = "INFO")
  $line = "[{0}] [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Level, $Message
  Write-Host $line
  Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

function Get-EnvValue {
  param([string]$Path, [string]$Key)
  if (-not (Test-Path $Path)) { return $null }
  $line = Select-String -Path $Path -Pattern ("^" + [regex]::Escape($Key) + "=") |
    Select-Object -Last 1
  if (-not $line) { return $null }
  $value = ($line.Line -split "=", 2)[1]
  return $value.Trim().Trim('"').Trim("'")
}

function Format-Cmd {
  param([string[]]$Parts)
  return ($Parts | ForEach-Object {
    if ($_ -match "\s") { '"' + $_ + '"' } else { $_ }
  }) -join " "
}

$Compose = @("compose", "--env-file", $EnvFile, "-f", $ComposeFile)
if ($ProdOverlay -or $Target -eq "production") { $Compose += @("-f", "docker-compose.prod.yml") }

function Invoke-Compose {
  param([string[]]$CommandArgs, [string]$Description)
  $cmd = $Compose + $CommandArgs
  Write-Log ("$Description -> " + (Format-Cmd $cmd))
  if (-not $Execute) { return }
  & docker @cmd
  if ($LASTEXITCODE -ne 0) { throw "Falló: $Description (exit $LASTEXITCODE)" }
}

function Invoke-PythonScript {
  param([string]$Script, [string[]]$ScriptArgs, [string]$Description)
  $parts = @("compose", "--env-file", $EnvFile, "-f", $ComposeFile)
  if ($ProdOverlay -or $Target -eq "production") { $parts += @("-f", "docker-compose.prod.yml") }
  $parts += @("exec", "-T", "api", "python", "/srv/scripts/$Script") + $ScriptArgs
  Write-Log ("$Description -> " + (Format-Cmd $parts))
  if (-not $Execute) { return }
  & docker @parts
  if ($LASTEXITCODE -ne 0) { throw "Falló: $Description (exit $LASTEXITCODE)" }
}

function Get-AlembicRevision {
  $parts = $Compose + @("exec", "-T", "api", "alembic", "-c", "alembic.ini", "current")
  if (-not $Execute) { return "current-revision-simulada" }
  $output = & docker @parts 2>&1
  if ($LASTEXITCODE -ne 0) { throw "No se pudo leer alembic current" }
  $line = ($output | Select-Object -Last 1)
  return ($line -replace "\s+.*$", "").Trim()
}

function New-VerifiedBackup {
  $backupPath = Join-Path $Root $BackupDir
  New-Item -ItemType Directory -Force -Path $backupPath | Out-Null
  $remote = "/tmp/legal-modules-$Timestamp.dump"
  $local = Join-Path $backupPath "judicial-$Timestamp.dump"
  $db = Get-EnvValue $EnvFile "POSTGRES_DB"
  $user = Get-EnvValue $EnvFile "POSTGRES_SUPERUSER"
  if (-not $db -or -not $user) { throw "Faltan POSTGRES_DB/POSTGRES_SUPERUSER en $EnvFile" }

  Invoke-Compose -CommandArgs @("exec", "-T", "postgres", "sh", "-c",
    "pg_dump -U $user -Fc $db > $remote") -Description "Backup pg_dump -Fc ($db)"

  Invoke-Compose -CommandArgs @("exec", "-T", "postgres", "pg_restore", "-l", $remote) `
    -Description "Verificar backup con pg_restore -l"

  if (-not $Execute) {
    Write-Log "[dry-run] backup quedaría en $local (verificado con pg_restore -l)"
    return $local
  }

  & docker @($Compose + @("cp", "postgres:$remote", $local))
  if ($LASTEXITCODE -ne 0) { throw "No se pudo copiar el backup al host" }
  Invoke-Compose -CommandArgs @("exec", "-T", "postgres", "rm", "-f", $remote) -Description "Limpiar dump temporal"
  $size = (Get-Item $local).Length
  if ($size -lt 1024) { throw "Backup sospechosamente pequeño: $size bytes" }
  Write-Log "Backup verificado: $local (${size} bytes)"
  return $local
}

function Get-SqlScalar {
  param([string]$Sql)
  $db = Get-EnvValue $EnvFile "POSTGRES_DB"
  $user = Get-EnvValue $EnvFile "POSTGRES_SUPERUSER"
  $parts = $Compose + @("exec", "-T", "postgres", "psql", "-U", $user, "-d", $db, "-tAc", $Sql)
  if (-not $Execute) {
    Write-Log "[dry-run] SQL: $Sql"
    return "0"
  }
  $value = & docker @parts
  if ($LASTEXITCODE -ne 0) { throw "Falló SQL: $Sql" }
  return ($value | Out-String).Trim()
}

function Invoke-Smoke {
  Write-Log "Smoke: health $SmokeBaseUrl"
  if ($Execute) {
    $health = Invoke-RestMethod -Uri $SmokeBaseUrl -Method Get -TimeoutSec 15
    if ($null -eq $health) { throw "Health vacío" }
    Write-Log ("Health OK: " + ($health | ConvertTo-Json -Compress))
  } else {
    Write-Log "[dry-run] GET $SmokeBaseUrl"
  }
  $checks = @(
    @("legal_rules activas", "SELECT count(*) FROM legal_rules WHERE is_active"),
    @("auditorías", "SELECT count(*) FROM answer_audits"),
    @("hechos", "SELECT count(*) FROM facts")
  )
  foreach ($check in $checks) {
    $value = Get-SqlScalar -Sql $check[1]
    Write-Log ("Smoke {0}: {1}" -f $check[0], $value)
  }
  if ($Execute) {
    $rules = [int](Get-SqlScalar -Sql "SELECT count(*) FROM legal_rules WHERE is_active")
    if ($rules -le 0) { throw "No hay normas activas; el gate bloqueará todo. Importa el corpus con -ImportCorpus." }
  }
}

function Invoke-Rollback {
  param([string]$Reason)
  Write-Log "ROLLBACK solicitado: $Reason" "ERROR"
  if ($RollbackTo) {
    Invoke-Compose -CommandArgs @("exec", "-T", "api", "alembic", "-c", "alembic.ini", "downgrade", $RollbackTo) `
      -Description "Rollback Alembic a $RollbackTo"
  } else {
    Write-Log "Sin -RollbackTo: no se toca el esquema. Define la revisión explícitamente." "WARN"
  }
  if ($RestoreBackup -and $script:BackupFile) {
    Write-Log "Restaurando backup $script:BackupFile (destructivo)" "WARN"
    if ($Execute) {
      & docker @($Compose + @("cp", $script:BackupFile, "postgres:/tmp/restore.dump"))
      if ($LASTEXITCODE -ne 0) { throw "No se pudo copiar el backup al contenedor" }
      $db = Get-EnvValue $EnvFile "POSTGRES_DB"
      $user = Get-EnvValue $EnvFile "POSTGRES_SUPERUSER"
      Invoke-Compose -CommandArgs @("exec", "-T", "postgres", "pg_restore", "-U", $user, "-d", $db,
        "--clean", "--if-exists", "/tmp/restore.dump") -Description "Restaurar backup"
    }
  }
}

# ---------------------------------------------------------------------------
# 0. Preflight
# ---------------------------------------------------------------------------
Write-Log "=== Despliegue módulos legales ==="
Write-Log "Target=$Target Execute=$Execute EnvFile=$EnvFile Compose=$ComposeFile"
if (-not (Test-Path $EnvFile)) { throw "No existe $EnvFile" }
if (-not (Test-Path $ComposeFile)) { throw "No existe $ComposeFile" }
if ($Target -eq "production") {
  if (-not $AuthorizeProduction) { throw "Producción exige -AuthorizeProduction" }
  if ($ConfirmProduction -cne "DEPLOY-PRODUCTION") {
    throw 'Producción exige -ConfirmProduction "DEPLOY-PRODUCTION"'
  }
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw "docker no está en PATH" }
if ($Execute) {
  & docker compose version | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "docker compose v2 no disponible" }
}

$current = Get-AlembicRevision
Write-Log "Revisión Alembic actual: $current"
$destination = if ($ToHead) { "head" } else { $TargetRevision }
Write-Log "Revisión destino: $destination"

# ---------------------------------------------------------------------------
# 1. Backup (siempre se simula o se ejecuta y verifica)
# ---------------------------------------------------------------------------
$script:BackupFile = $null
try {
  $script:BackupFile = New-VerifiedBackup
} catch {
  if ($Target -eq "production") { throw "Backup no verificado; se aborta producción. $_" }
  Write-Log "Backup falló (target no producción): $_" "WARN"
}

try {
  # -------------------------------------------------------------------------
  # 2. Migración
  # -------------------------------------------------------------------------
  Invoke-Compose -CommandArgs @("exec", "-T", "api", "alembic", "-c", "alembic.ini",
    "upgrade", $destination) -Description "Alembic upgrade $destination"
  $after = Get-AlembicRevision
  Write-Log "Revisión después de migrar: $after"

  # -------------------------------------------------------------------------
  # 3. Corpus (opcional)
  # -------------------------------------------------------------------------
  if ($ImportCorpus) {
    Invoke-PythonScript -Script "import_legal_rules.py" `
      -ScriptArgs @("--dir", "data/legal_rules/co", "--all") `
      -Description "Importar corpus legal en todas las organizaciones"
  } else {
    Write-Log "Corpus: omitido (-ImportCorpus no especificado)"
  }

  # -------------------------------------------------------------------------
  # 4-6. Backfill / reindex / grafo
  # -------------------------------------------------------------------------
  $scope = if ($CaseId) { @("--case-id", $CaseId) } else { @("--all") }
  Invoke-PythonScript -Script "backfill_legal_modules.py" -ScriptArgs $scope `
    -Description "Backfill del ledger de hechos"
  $reindexArgs = $scope
  if ($ReindexLocalHash) {
    if ($Target -eq "production") { throw "ReindexLocalHash no está permitido en producción" }
    $reindexArgs += "--local-hash"
  }
  Invoke-PythonScript -Script "reindex_facts.py" -ScriptArgs $reindexArgs `
    -Description "Reindexar chunks estructurados"
  Invoke-PythonScript -Script "rebuild_graphs.py" -ScriptArgs $scope `
    -Description "Reconstruir grafo"

  # -------------------------------------------------------------------------
  # 7. Smoke
  # -------------------------------------------------------------------------
  Invoke-Smoke

  Write-Log "=== Despliegue terminado ($Target) ==="
  exit 0
}
catch {
  Write-Log "ERROR: $_" "ERROR"
  try { Invoke-Rollback -Reason "$_" } catch { Write-Log "Rollback también falló: $_" "ERROR" }
  exit 1
}
