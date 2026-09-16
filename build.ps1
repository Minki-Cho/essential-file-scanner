[CmdletBinding()]
param(
    [string]$Python = "python",
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot

Push-Location -LiteralPath $ProjectRoot
try {
    & $Python -c "import PySide6, PyInstaller" 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "PySide6/PyInstaller가 없습니다. 먼저 'python -m pip install -r requirements-dev.txt'를 실행하세요."
    }

    $arguments = @("-m", "PyInstaller", "--noconfirm")
    if ($Clean) {
        $arguments += "--clean"
    }
    $arguments += "RequiredFileScanner.spec"

    & $Python @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller 빌드에 실패했습니다."
    }

    Write-Host "빌드 완료: $ProjectRoot\dist\RequiredFileScanner.exe"
}
finally {
    Pop-Location
}
