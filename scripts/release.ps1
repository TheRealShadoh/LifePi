# Build LifePi for Android and (with -Play) upload it to Google Play, from this PC.
#
#   powershell -File scripts\release.ps1                        # tests -> emulator test -> signed bundle
#   powershell -File scripts\release.ps1 -Play                  # ...then to closed testing (alpha)
#   powershell -File scripts\release.ps1 -Play -Track internal
#   powershell -File scripts\release.ps1 -Play -PasswordFromClipboard
#
# Needs, under %USERPROFILE%\dev\keys (never in the repo):
#   lifepi-upload.p12   the upload key (its password: typed at a hidden prompt, or -PasswordFromClipboard)
#   lifepi-admob.json   {"app": "ca-app-pub-...~...", "banner": "ca-app-pub-.../..."} - not secret, but
#                       without it the bundle would carry Google's test ads, so -Play refuses
#   lifepi-play.json    the Play service-account key (scripts/play_upload.py)
# Every upload needs a higher version code: it comes from VERSION in lifepi.py (1.13.0 -> 1013000),
# so bump VERSION before releasing again.
param(
    [switch]$Play,
    [string]$Track = "alpha",
    [switch]$PasswordFromClipboard,
    [switch]$SkipTests
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$dev = Join-Path $env:USERPROFILE "dev"
$keys = Join-Path $dev "keys"
$py = Join-Path $dev "lifepi-venv\Scripts\python.exe"
function Step($text) { Write-Host ""; Write-Host "== $text" -ForegroundColor Cyan }
function Check($ok, $text) { if (-not $ok) { Write-Host "FAILED: $text" -ForegroundColor Red; throw $text } }

Set-Location $repo
Check (Test-Path $py) "no Python environment yet: run mobile\local_test.ps1 once (it sets one up)"
$version = [regex]::Match((Get-Content lifepi.py -Raw), '(?m)^VERSION = "([^"]+)"').Groups[1].Value
$p = $version.Split(".") | ForEach-Object { [int]$_ }
$code = $p[0] * 1000000 + $p[1] * 1000 + $p[2]
Write-Host "LifePi $version (version code $code), track $Track"

# ---- 1. tests, and the emulator check on a fresh debug build
if (-not $SkipTests) {
    Step "Tests"
    $env:SDL_VIDEODRIVER = "dummy"; $env:SDL_AUDIODRIVER = "dummy"
    & $py tests/smoke_test.py | Select-Object -Last 3
    Check ($LASTEXITCODE -eq 0) "smoke tests failed"
    & $py tests/companion_test.py | Select-Object -Last 2
    Check ($LASTEXITCODE -eq 0) "companion tests failed"
    Step "Debug APK on the emulators"
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repo "mobile\local_test.ps1")
    Check ($LASTEXITCODE -eq 0) "emulator test failed"
}

# ---- 2. Play first: is this version code new?
if ($Play) {
    Step "Checking Play"
    & $py -c "import google.auth, requests" 2>$null
    if ($LASTEXITCODE -ne 0) { & $py -m pip install -q google-auth requests }
    $out = & $py scripts/play_upload.py --check 2>&1
    $out | ForEach-Object { Write-Host $_ }
    Check ($LASTEXITCODE -eq 0) "Play check failed (see above)"
    $top = [regex]::Match(($out -join "`n"), 'highest version code in Play: (\d+)').Groups[1].Value
    if ($top -and [int]$top -ge $code) { throw "Play already has version code $top; bump VERSION in lifepi.py (now $version = $code)" }
}

# ---- 3. the signed bundle
Step "Signed release bundle"
$ks = Join-Path $keys "lifepi-upload.p12"
Check (Test-Path $ks) "no upload key at $ks"
$admob = Join-Path $keys "lifepi-admob.json"
if (Test-Path $admob) {
    $ids = Get-Content $admob -Raw | ConvertFrom-Json
    $env:ADMOB_APP_ID = $ids.app; $env:ADMOB_BANNER_ID = $ids.banner
} elseif ($Play) {
    throw "no $admob - a Play build without your AdMob IDs would show Google's test ads"
} else {
    Write-Host "no lifepi-admob.json: this bundle carries Google's test ads (fine for trying, not for Play)" -ForegroundColor Yellow
}
if ($PasswordFromClipboard) {
    $pw = Get-Clipboard -Raw
} else {
    $sec = Read-Host "Upload key password (hidden)" -AsSecureString
    $pw = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
}
$env:LIFEPI_UPLOAD_KEYSTORE = $ks
$env:LIFEPI_UPLOAD_STORE_PASSWORD = $pw.Trim()
$env:LIFEPI_VERSION = $version
$env:LIFEPI_VERSION_CODE = "$code"
$env:JAVA_HOME = Join-Path $dev "jdk21"
$env:ANDROID_HOME = Join-Path $dev "android-sdk"
try {
    & $py mobile/prepare.py                         # this time with the real banner ID
    Check ($LASTEXITCODE -eq 0) "prepare failed"
    Push-Location mobile
    npx cap sync android | Out-Null
    Pop-Location
    Push-Location mobile\android
    .\gradlew.bat --no-daemon bundleRelease
    $built = ($LASTEXITCODE -eq 0)
    Pop-Location
    Check $built "gradle bundleRelease failed"
} finally {
    Remove-Item Env:LIFEPI_UPLOAD_STORE_PASSWORD -ErrorAction SilentlyContinue
    $pw = $null
    if ($PasswordFromClipboard) { Set-Clipboard -Value " " }      # don't leave it on the clipboard
}
$aab = Join-Path $repo "mobile\android\app\build\outputs\bundle\release\app-release.aab"
Check (Test-Path $aab) "no bundle at $aab"
Write-Host "bundle: $aab"

# ---- 4. upload
if ($Play) {
    Step "Uploading to the $Track track"
    & $py scripts/play_upload.py $aab --track $Track
    Check ($LASTEXITCODE -eq 0) "upload failed"
    & $py scripts/play_upload.py --check
}
