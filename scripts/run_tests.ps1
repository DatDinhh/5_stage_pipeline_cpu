[CmdletBinding()]
param(
    [string]$PythonExe = $env:CPU_VERIFY_PYTHON,
    [ValidateSet('unit', 'directed', 'random', 'signoff')]
    [string]$Suite,
    [string[]]$Test,
    [uint32[]]$Seed,
    [string]$Sim,
    [string]$Vvp,
    [switch]$Waves,
    [switch]$List,
    [int]$MaxCycles,
    [int]$Timeout,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RunnerArgs
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $PSScriptRoot 'run_tests.py'

if (-not $PythonExe) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        $PythonExe = $pythonCommand.Source
    } else {
        $pyCommand = Get-Command py -ErrorAction SilentlyContinue
        if ($pyCommand) {
            $PythonExe = $pyCommand.Source
        }
    }
}

if (-not $PythonExe) {
    Write-Error 'Python was not found. Pass -PythonExe or set CPU_VERIFY_PYTHON.'
    exit 2
}

Push-Location $repoRoot
try {
    $forwardArgs = @()
    if ($PSBoundParameters.ContainsKey('Suite')) { $forwardArgs += @('--suite', $Suite) }
    foreach ($item in $Test) { $forwardArgs += @('--test', $item) }
    foreach ($item in $Seed) { $forwardArgs += @('--seed', [string]$item) }
    if ($PSBoundParameters.ContainsKey('Sim')) { $forwardArgs += @('--sim', $Sim) }
    if ($PSBoundParameters.ContainsKey('Vvp')) { $forwardArgs += @('--vvp', $Vvp) }
    if ($Waves) { $forwardArgs += '--waves' }
    if ($List) { $forwardArgs += '--list' }
    if ($PSBoundParameters.ContainsKey('MaxCycles')) { $forwardArgs += @('--max-cycles', [string]$MaxCycles) }
    if ($PSBoundParameters.ContainsKey('Timeout')) { $forwardArgs += @('--timeout', [string]$Timeout) }
    $forwardArgs += $RunnerArgs
    & $PythonExe $runner @forwardArgs
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
