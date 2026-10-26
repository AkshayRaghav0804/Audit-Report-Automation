[CmdletBinding()]
param(
    [int]$Port = 5433,
    [string]$PostgresMajorVersion = "17",
    [switch]$ChangePort
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$dataDirectory = Join-Path $projectRoot "data\postgres-local"
$logPath = Join-Path $projectRoot "data\postgres-local.log"
$credentialPath = Join-Path $projectRoot ".env.postgres.local"
$postgresBin = Join-Path $env:ProgramFiles "PostgreSQL\$PostgresMajorVersion\bin"
$initdb = Join-Path $postgresBin "initdb.exe"
$pgCtl = Join-Path $postgresBin "pg_ctl.exe"
$pgIsReady = Join-Path $postgresBin "pg_isready.exe"
$psql = Join-Path $postgresBin "psql.exe"
$createdb = Join-Path $postgresBin "createdb.exe"

function New-LocalPassword {
    $bytes = [byte[]]::new(24)
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    }
    finally {
        $generator.Dispose()
    }
    return "Aa1_" + ([BitConverter]::ToString($bytes) -replace "-", "")
}

function Read-LocalCredential {
    param([Parameter(Mandatory)][string]$Name)

    if (-not (Test-Path -LiteralPath $credentialPath)) {
        return $null
    }
    $line = Get-Content -LiteralPath $credentialPath |
        Where-Object { $_ -like "$Name=*" } |
        Select-Object -First 1
    if (-not $line) {
        return $null
    }
    return $line.Substring($Name.Length + 1)
}

function Protect-LocalSecretFile {
    param([Parameter(Mandatory)][string]$Path)

    $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    if ($null -eq $currentUser) {
        throw "Could not identify the current Windows user for local secret protection."
    }
    $icacls = Join-Path $env:SystemRoot "System32\icacls.exe"
    if (-not (Test-Path -LiteralPath $icacls)) {
        throw "The Windows ACL utility was not found: $icacls"
    }
    & $icacls $Path `
        /inheritance:r `
        /grant:r "*$($currentUser.Value):(F)" "*S-1-5-18:(F)" "*S-1-5-32-544:(F)" *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not restrict access to the local secret file."
    }
}

function Save-LocalCredentials {
    param(
        [Parameter(Mandatory)][string]$AdministratorPassword,
        [Parameter(Mandatory)][string]$ApplicationPassword
    )

    $applicationUrl = (
        "postgresql+asyncpg://qc_app:$ApplicationPassword@127.0.0.1:$Port/qc_automation"
    )
    $testUrl = (
        "postgresql+asyncpg://qc_app:$ApplicationPassword@127.0.0.1:$Port/qc_automation_test"
    )
    $content = @(
        "# Generated current-user PostgreSQL credentials. Do not commit or share this file."
        "QC_DATABASE_URL=$applicationUrl"
        "QC_POSTGRES_TEST_URL=$testUrl"
        "QC_POSTGRES_APP_PASSWORD=$ApplicationPassword"
        "QC_POSTGRES_ADMIN_USER=postgres"
        "QC_POSTGRES_ADMIN_PASSWORD=$AdministratorPassword"
        "QC_POSTGRES_LOCAL_PORT=$Port"
    ) -join [Environment]::NewLine
    [IO.File]::WriteAllText($credentialPath, $content + [Environment]::NewLine)
    Protect-LocalSecretFile -Path $credentialPath
}

foreach ($tool in @($initdb, $pgCtl, $pgIsReady, $psql, $createdb)) {
    if (-not (Test-Path -LiteralPath $tool)) {
        throw "Required PostgreSQL tool was not found: $tool"
    }
}

$superPassword = Read-LocalCredential -Name "QC_POSTGRES_ADMIN_PASSWORD"
$appPassword = Read-LocalCredential -Name "QC_POSTGRES_APP_PASSWORD"
$savedPort = Read-LocalCredential -Name "QC_POSTGRES_LOCAL_PORT"
$clusterExists = Test-Path -LiteralPath (Join-Path $dataDirectory "PG_VERSION")
if ($clusterExists -and (-not $superPassword -or -not $appPassword)) {
    throw "The local cluster exists, but .env.postgres.local does not contain its credentials."
}

# A responding server on this port may be an unrelated Windows service. Never
# authenticate, provision, or rewrite saved settings until ownership is checked.
$clusterRunning = $false
if ($clusterExists) {
    & $pgCtl -D $dataDirectory status *> $null
    $clusterRunning = $LASTEXITCODE -eq 0
}
if ($ChangePort -and (-not $clusterExists -or $clusterRunning)) {
    throw "-ChangePort requires an existing, stopped project cluster."
}
if ($savedPort -and [int]$savedPort -ne $Port -and -not $ChangePort) {
    throw (
        "The existing local cluster uses port $savedPort; rerun with -Port $savedPort. " +
        "To move a stopped project cluster, use -Port $Port -ChangePort."
    )
}

$portListener = [System.Net.Sockets.TcpClient]::new()
try {
    $connection = $portListener.ConnectAsync("127.0.0.1", $Port)
    $portOccupied = $connection.Wait(1000) -and $portListener.Connected
}
catch [System.AggregateException] {
    $portOccupied = $false
}
finally {
    $portListener.Dispose()
}
if ($portOccupied -and -not $clusterRunning) {
    throw (
        "Port $Port is already used by another process; the project cluster is stopped. " +
        "Leave that process running and choose a free port with -Port <unused-port> -ChangePort."
    )
}

if (-not $superPassword) {
    $superPassword = New-LocalPassword
}
if (-not $appPassword) {
    $appPassword = New-LocalPassword
}
if (-not $clusterExists) {
    Save-LocalCredentials `
        -AdministratorPassword $superPassword `
        -ApplicationPassword $appPassword
    New-Item -ItemType Directory -Path $dataDirectory -Force | Out-Null
    $passwordFile = Join-Path $projectRoot "data\.postgres-init-password"
    try {
        [IO.File]::WriteAllText($passwordFile, $superPassword + [Environment]::NewLine)
        Protect-LocalSecretFile -Path $passwordFile
        & $initdb `
            -D $dataDirectory `
            -U postgres `
            --encoding=UTF8 `
            --auth-local=scram-sha-256 `
            --auth-host=scram-sha-256 `
            --pwfile=$passwordFile
        if ($LASTEXITCODE -ne 0) {
            throw "PostgreSQL cluster initialization failed."
        }
    }
    finally {
        Remove-Item -LiteralPath $passwordFile -Force -ErrorAction SilentlyContinue
    }
}

& $pgIsReady -h 127.0.0.1 -p $Port -U postgres -d postgres *> $null
if ($LASTEXITCODE -ne 0) {
    & $pgCtl -D $dataDirectory -l $logPath -o "-p $Port -h 127.0.0.1" start
    if ($LASTEXITCODE -ne 0) {
        throw "Could not start the current-user PostgreSQL cluster."
    }
}

$env:PGPASSWORD = $superPassword
$ready = $false
for ($attempt = 1; $attempt -le 20; $attempt++) {
    & $pgIsReady -h 127.0.0.1 -p $Port -U postgres -d postgres *> $null
    if ($LASTEXITCODE -eq 0) {
        $ready = $true
        break
    }
    Start-Sleep -Seconds 1
}
if (-not $ready) {
    throw "PostgreSQL did not become ready on 127.0.0.1:$Port."
}

$serverDirectory = [string](& $psql -X -w -tA -h 127.0.0.1 -p $Port -U postgres -d postgres `
    -c "SHOW data_directory")
if ($LASTEXITCODE -ne 0) {
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
    throw "Could not verify the project cluster. Check its saved administrator credentials."
}
if ([IO.Path]::GetFullPath($serverDirectory.Trim()) -ine [IO.Path]::GetFullPath($dataDirectory)) {
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
    throw "Port $Port belongs to a different PostgreSQL cluster. No provisioning was performed."
}

$roleExists = [string](& $psql -X -tA -h 127.0.0.1 -p $Port -U postgres -d postgres `
    -c "SELECT 1 FROM pg_roles WHERE rolname = 'qc_app'")
$roleExists = if ($null -eq $roleExists) { "" } else { $roleExists.Trim() }
if ($LASTEXITCODE -ne 0) {
    throw "Could not inspect PostgreSQL roles."
}
if ($roleExists -ne "1") {
    # Send the password over standard input so it is not visible in the psql command line.
    $escapedAppPassword = $appPassword.Replace("'", "''")
    $createRoleSql = "CREATE ROLE qc_app LOGIN PASSWORD '$escapedAppPassword';"
    $createRoleSql | & $psql -X -v ON_ERROR_STOP=1 `
        -h 127.0.0.1 -p $Port -U postgres -d postgres
    $createRoleSql = $null
    $escapedAppPassword = $null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create the qc_app role."
    }
}

foreach ($database in @("qc_automation", "qc_automation_test")) {
    $databaseExists = [string](& $psql -X -tA -h 127.0.0.1 -p $Port -U postgres -d postgres `
        -c "SELECT 1 FROM pg_database WHERE datname = '$database'")
    $databaseExists = if ($null -eq $databaseExists) { "" } else { $databaseExists.Trim() }
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect PostgreSQL databases."
    }
    if ($databaseExists -ne "1") {
        & $createdb -h 127.0.0.1 -p $Port -U postgres -O qc_app $database
        if ($LASTEXITCODE -ne 0) {
            throw "Could not create database $database."
        }
    }
}

Save-LocalCredentials `
    -AdministratorPassword $superPassword `
    -ApplicationPassword $appPassword

Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
Write-Output "Current-user PostgreSQL is ready on 127.0.0.1:$Port."
Write-Output "Local connection settings were written to $credentialPath."
