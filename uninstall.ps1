param(
  [Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments
)
$ErrorActionPreference = 'Stop'

$pyExe = $null
$pyPrefix = @()
foreach ($cmdName in @('python', 'python3')) {
  $cmd = Get-Command $cmdName -CommandType Application -ErrorAction SilentlyContinue
  if ($cmd) {
    $null = & $cmd.Source -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
    if ($LASTEXITCODE -eq 0) {
      $pyExe = $cmd.Source
      break
    }
  }
}
if (-not $pyExe) {
  $pyLauncher = Get-Command py -CommandType Application -ErrorAction SilentlyContinue
  if ($pyLauncher) {
    $null = & $pyLauncher.Source -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
    if ($LASTEXITCODE -eq 0) {
      $pyExe = $pyLauncher.Source
      $pyPrefix = @('-3')
    }
  }
}
if (-not $pyExe) {
  throw 'Python 3.10+ not found.'
}

$uninstaller = Join-Path $PSScriptRoot 'uninstall.py'
& $pyExe @pyPrefix $uninstaller @Arguments
exit $LASTEXITCODE
