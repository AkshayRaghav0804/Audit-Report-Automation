[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
Set-Location -LiteralPath $projectRoot

try {
    if (-not (Test-Path -LiteralPath $venvPython)) {
        $pythonCommand = $null
        $pythonArguments = @()
        foreach ($candidate in @("py.exe", "python.exe")) {
            $available = Get-Command $candidate -ErrorAction SilentlyContinue
            if (-not $available) { continue }
            $candidateArguments = if ($candidate -eq "py.exe") { @("-3") } else { @() }
            & $available.Source @candidateArguments -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" *> $null
            if ($LASTEXITCODE -eq 0) {
                $pythonCommand = $available.Source
                $pythonArguments = $candidateArguments
                break
            }
        }
        if (-not $pythonCommand) {
            throw "Install Python 3.11 or later with the Python launcher or PATH option, then rerun setup.cmd."
        }
        Write-Output "Creating the project virtual environment..."
        & $pythonCommand @pythonArguments -m venv (Join-Path $projectRoot ".venv")
        if ($LASTEXITCODE -ne 0) { throw "Could not create the virtual environment." }
    }

    & $venvPython -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
    if ($LASTEXITCODE -ne 0) { throw "The existing virtual environment needs Python 3.11 or later." }

    Write-Output "Installing application, development, and migration dependencies..."
    & $venvPython -m pip install -e ".[dev,migration]"
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed. Check internet access and rerun setup.cmd." }

    & $venvPython -m scripts.local_setup
    if ($LASTEXITCODE -ne 0) { throw "Local configuration or database setup failed." }
}
catch {
    Write-Output ("ERROR: " + $_.Exception.Message)
    exit 1
}
exit 0
