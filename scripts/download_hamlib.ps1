param(
    [string]$Version = "4.7.2"
)

$ErrorActionPreference = "Stop"

$knownHashes = @{
    "4.7.2" = "8553bc6c5c6032e8debf99c017e98f58fed7e07e7c25d04815dc3e8bbe3304c7"
}

if (-not $knownHashes.ContainsKey($Version)) {
    throw "No trusted SHA-256 is configured for Hamlib $Version."
}

$root = Split-Path -Parent $PSScriptRoot
$target = Join-Path $root "hamlib\bin"
$mainDll = Join-Path $target "libhamlib-4.dll"
if (Test-Path $mainDll) {
    Write-Host "Hamlib already prepared: $mainDll"
    exit 0
}

$work = Join-Path $env:TEMP "HFWeatherFax-Hamlib-$Version"
$zip = Join-Path $work "hamlib-w64-$Version.zip"
$extract = Join-Path $work "extract"
$url = "https://github.com/Hamlib/Hamlib/releases/download/$Version/hamlib-w64-$Version.zip"

Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $work | Out-Null
Write-Host "Downloading official Hamlib $Version x64..."
Invoke-WebRequest -Uri $url -OutFile $zip

$actual = (Get-FileHash -Path $zip -Algorithm SHA256).Hash.ToLowerInvariant()
$expected = $knownHashes[$Version].ToLowerInvariant()
if ($actual -ne $expected) {
    throw "Hamlib SHA-256 mismatch. Expected $expected, got $actual."
}
Write-Host "Hamlib SHA-256 verified: $actual"

Expand-Archive -Path $zip -DestinationPath $extract -Force
$dll = Get-ChildItem -Path $extract -Recurse -File -Filter "libhamlib-4.dll" | Select-Object -First 1
if (-not $dll) {
    throw "libhamlib-4.dll was not found in the Hamlib archive."
}

New-Item -ItemType Directory -Path $target -Force | Out-Null
Get-ChildItem -Path $dll.Directory.FullName -File -Filter "*.dll" | Copy-Item -Destination $target -Force
Set-Content -Path (Join-Path $root "hamlib\VERSION.txt") -Value "Hamlib $Version x64`n$url`nSHA256 $expected" -Encoding UTF8
Write-Host "Hamlib DLLs prepared in $target"
