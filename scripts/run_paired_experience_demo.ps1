param(
    [string]$Python = 'python'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$previousUri = [Environment]::GetEnvironmentVariable('MOZAIKS_PAIRED_DEMO_MONGO_URI', 'Process')
$previousOutput = [Environment]::GetEnvironmentVariable('MOZAIKS_PAIRED_DEMO_OUTPUT', 'Process')
$containerId = $null

Push-Location $repoRoot
try {
    $requirement = Select-String -Path 'requirements.txt' -Pattern '^ag2\[.*\]==([0-9.]+)' | Select-Object -First 1
    if (-not $requirement) { throw 'The pinned AG2 requirement was not found.' }
    $expectedAg2 = $requirement.Matches[0].Groups[1].Value
    $actualAg2 = & $Python -c 'import importlib.metadata as m; print(m.version("ag2"))'
    if ($LASTEXITCODE -ne 0 -or $actualAg2.Trim() -ne $expectedAg2) {
        throw "This demo requires the repo AG2 pin $expectedAg2. The selected Python has $actualAg2."
    }

    docker image inspect mongo:7 *> $null
    if ($LASTEXITCODE -ne 0) { throw 'The local mongo:7 Docker image is required.' }
    $containerId = (docker run --detach --rm --publish 127.0.0.1::27017 mongo:7).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $containerId) { throw 'Could not start disposable local MongoDB.' }
    $portLine = (docker port $containerId 27017/tcp).Trim()
    if ($LASTEXITCODE -ne 0 -or $portLine -notmatch '^127\.0\.0\.1:(\d+)$') {
        throw "Could not read the local MongoDB port: $portLine"
    }
    $env:MOZAIKS_PAIRED_DEMO_MONGO_URI = "mongodb://127.0.0.1:$($Matches[1])"
    $env:MOZAIKS_PAIRED_DEMO_OUTPUT = Join-Path $repoRoot '.local/evidence/paired-experience-960'
    New-Item -ItemType Directory -Force -Path $env:MOZAIKS_PAIRED_DEMO_OUTPUT | Out-Null

    & $Python -m pytest -q --no-cov tests/test_paired_experience_demo_960.py --tb=short
    if ($LASTEXITCODE -ne 0) { throw "Paired experience acceptance failed with exit code $LASTEXITCODE." }
    Write-Host "Private bundle and host receipts: $env:MOZAIKS_PAIRED_DEMO_OUTPUT"
}
finally {
    [Environment]::SetEnvironmentVariable('MOZAIKS_PAIRED_DEMO_MONGO_URI', $previousUri, 'Process')
    [Environment]::SetEnvironmentVariable('MOZAIKS_PAIRED_DEMO_OUTPUT', $previousOutput, 'Process')
    if ($containerId) { docker stop $containerId *> $null }
    Pop-Location
}
