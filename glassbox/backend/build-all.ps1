Param()

$ErrorActionPreference = 'Stop'

# Build all packages with package name 'main' into ./bin
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Push-Location $scriptDir
try {
    $binDir = Join-Path $scriptDir 'bin'
    if (-Not (Test-Path $binDir)) { New-Item -ItemType Directory -Path $binDir | Out-Null }

    Write-Host "Enumerating packages..."
    $pkgs = & go list ./...
    foreach ($pkg in $pkgs) {
        $pkg = $pkg.Trim()
        if ($pkg -eq '') { continue }
        $pkgName = Split-Path $pkg -Leaf
        $pkgType = (& go list -f '{{.Name}}' $pkg).Trim()
        if ($pkgType -ne 'main') { continue }

        $out = Join-Path $binDir $pkgName
        if ($IsWindows) { $out = $out + '.exe' }
        Write-Host "Building $pkg -> $out"
        & go build -o $out $pkg
    }
    Write-Host "Build complete. Binaries are in: $binDir"
} finally {
    Pop-Location
}
