param(
  [Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments
)
$ErrorActionPreference = 'Stop'

function Test-Python310Plus {
  param([string]$Exe, [string[]]$PrefixArgs = @())
  try {
    $out = & $Exe @PrefixArgs -c "import sys; print(sys.version_info >= (3, 10))" 2>$null
    return ($LASTEXITCODE -eq 0 -and "$out".Trim() -eq 'True')
  } catch {
    return $false
  }
}

$pyExe = $null
$pyPrefix = @()

foreach ($cmdName in @('python', 'python3')) {
  $cmd = Get-Command $cmdName -CommandType Application -ErrorAction SilentlyContinue
  if ($cmd -and (Test-Python310Plus -Exe $cmd.Source)) {
    $pyExe = $cmd.Source
    break
  }
}

if (-not $pyExe) {
  $pyLauncher = Get-Command py -CommandType Application -ErrorAction SilentlyContinue
  if ($pyLauncher -and (Test-Python310Plus -Exe $pyLauncher.Source -PrefixArgs @('-3'))) {
    $pyExe = $pyLauncher.Source
    $pyPrefix = @('-3')
  }
}

if (-not $pyExe) {
  throw 'Python 3.10+ not found. Please install Python 3.10 or newer (recommended 3.11+).'
}

$installer = Join-Path $PSScriptRoot 'install.py'
& $pyExe @pyPrefix $installer @Arguments
exit $LASTEXITCODE
