# LifePi for Android, built and tested on this PC's emulators - the same checks GitHub Actions
# runs (.github/workflows/android.yml), on the "helper" and "person" emulators.
#
#   powershell -File mobile\local_test.ps1                 # build, install on both, test both
#   powershell -File mobile\local_test.ps1 -SkipWeb        # reuse the web build + bundled runtime
#   powershell -File mobile\local_test.ps1 -SkipBuild      # just test the last APK
#   powershell -File mobile\local_test.ps1 -Serial emulator-5556
#
# First run sets up what's missing, all under %USERPROFILE%\dev: JDK 21 (Capacitor 8 needs it;
# the JDK 17 there stays for other projects) and a venv with pygbag, made from the installed
# Python (Windows Application Control blocks the Pythons uv downloads on this PC).
# Debug builds only, with Google's sample ads - nothing here signs a release.
param(
    [string[]]$Serial = @("emulator-5554", "emulator-5556"),
    [switch]$SkipWeb,
    [switch]$SkipBuild
)
# "Continue": Windows PowerShell 5 turns any stderr line of a program (a Python warning, say)
# into a fatal error under "Stop"; every step checks its exit code instead
$ErrorActionPreference = "Continue"
$env:PYTHONWARNINGS = "ignore"
$ProgressPreference = "SilentlyContinue"            # PowerShell 5 downloads crawl with a progress bar
$repo = Split-Path -Parent $PSScriptRoot
$dev = Join-Path $env:USERPROFILE "dev"
$env:ANDROID_HOME = Join-Path $dev "android-sdk"
$env:ANDROID_SDK_ROOT = $env:ANDROID_HOME
$env:JAVA_HOME = Join-Path $dev "jdk21"
$env:PATH = "$env:JAVA_HOME\bin;$env:ANDROID_HOME\platform-tools;$env:PATH"
$env:SDL_VIDEODRIVER = "dummy"
$venv = Join-Path $dev "lifepi-venv"
$py = Join-Path $venv "Scripts\python.exe"

function Step($text) { Write-Host ""; Write-Host "== $text" -ForegroundColor Cyan }
function Check($ok, $text) { if (-not $ok) { Write-Host "FAILED: $text" -ForegroundColor Red; throw $text } }

# ---- one-time setup
if (-not (Test-Path "$env:JAVA_HOME\bin\java.exe")) {
    Step "Installing JDK 21 (Eclipse Temurin) to $env:JAVA_HOME"
    $zip = Join-Path $env:TEMP "temurin21.zip"
    Invoke-WebRequest "https://api.adoptium.net/v3/binary/latest/21/ga/windows/x64/jdk/hotspot/normal/eclipse" -OutFile $zip
    $tmp = Join-Path $env:TEMP "temurin21"
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    Expand-Archive $zip $tmp
    Move-Item (Get-ChildItem $tmp -Directory | Select-Object -First 1).FullName $env:JAVA_HOME
    Remove-Item $zip, $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
if (-not (Test-Path $py)) {
    Step "Making the Python environment $venv"
    python -m venv $venv
    Check ($LASTEXITCODE -eq 0) "couldn't make the venv"
}
& $py -c "import pygbag, pygame, websocket, google.auth" 2>$null
if ($LASTEXITCODE -ne 0) {
    Step "Installing pygbag, pygame-ce, websocket-client, google-auth"
    & $py -m pip install -q "pygbag==0.9.3" pygame-ce websocket-client google-auth requests
    Check ($LASTEXITCODE -eq 0) "pip install failed"
}

# ---- build (debug APK)
$apk = Join-Path $repo "mobile\android\app\build\outputs\apk\debug\app-debug.apk"
if (-not $SkipBuild) {
    Push-Location $repo
    try {
        if (-not $SkipWeb) {
            Step "Web build"
            & $py web/build.py
            Check ($LASTEXITCODE -eq 0) "web build failed"
            Step "Bundling pygbag's runtime"
            Remove-Item "mobile\android\app\src\main\assets\cdn" -Recurse -Force -ErrorAction SilentlyContinue
            & $py mobile/vendor_cdn.py
            Check ($LASTEXITCODE -eq 0) "vendor_cdn failed"
        }
        Step "Preparing the page"
        & $py mobile/prepare.py
        Check ($LASTEXITCODE -eq 0) "prepare failed"
        Step "Capacitor sync"
        Push-Location mobile
        npm ci --no-audit --no-fund
        Check ($LASTEXITCODE -eq 0) "npm ci failed"
        npx cap sync android
        Check ($LASTEXITCODE -eq 0) "cap sync failed"
        Pop-Location
        Step "Gradle: debug APK"
        Push-Location mobile\android
        .\gradlew.bat --no-daemon assembleDebug
        Check ($LASTEXITCODE -eq 0) "gradle failed"
        Pop-Location
    } finally { Pop-Location }
}
Check (Test-Path $apk) "no APK at $apk - build first"

# ---- test on each emulator
$failed = @()
foreach ($s in $Serial) {
    Step "Testing on $s"
    $state = (adb -s $s get-state 2>$null)
    if ($state -ne "device") { Write-Host "$s isn't running - skipped" -ForegroundColor Yellow; $failed += "$s (not running)"; continue }
    $env:LIFEPI_SHOTS = Join-Path $dev "lifepi-shots\$s"
    New-Item -ItemType Directory -Force $env:LIFEPI_SHOTS | Out-Null
    & $py (Join-Path $repo "mobile\emulator_test.py") --serial $s $apk
    if ($LASTEXITCODE -ne 0) { $failed += $s } else { Write-Host "$s passed; screenshots in $env:LIFEPI_SHOTS" -ForegroundColor Green }
}
Write-Host ""
if ($failed) { Write-Host "FAILED: $($failed -join ', ')" -ForegroundColor Red; exit 1 }
Write-Host "All emulators passed." -ForegroundColor Green
