# Activate the portable Node runtime for this user (no administrator rights).
#
# Why: REA 6.10.1 requires Node >=24.22.1, the machine-wide install is 24.18.0,
# and the UAC-elevated upgrade is unavailable. This prepends the verified
# portable Node to the *user* PATH and records ZERO_NODE_DIR, which the engine
# reads in engine/v2/mcp_client.py. Reversible: see -Restore with a backup.
param(
    [string]$NodeDir = "$env:LOCALAPPDATA\Programs\node-v24.22.3\node-v24.22.3-win-x64",
    [switch]$Restore
)

$ErrorActionPreference = "Stop"
$backupDir = Join-Path $env:LOCALAPPDATA "ZeroTerminal"
$backupFile = Join-Path $backupDir "user-path-before-node-upgrade.txt"
$key = Get-Item "HKCU:\Environment"

function Get-RawUserPath {
    (Get-Item "HKCU:\Environment").GetValue(
        "Path", "", [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
}

if ($Restore) {
    if (-not (Test-Path $backupFile)) { throw "No backup at $backupFile" }
    $saved = Get-Content $backupFile -Raw
    $key.SetValue("Path", $saved, [Microsoft.Win32.RegistryValueKind]::ExpandString)
    [Environment]::SetEnvironmentVariable("ZERO_NODE_DIR", $null, "User")
    Write-Host "Restored the previous user PATH and removed ZERO_NODE_DIR."
    exit 0
}

$nodeExe = Join-Path $NodeDir "node.exe"
if (-not (Test-Path $nodeExe)) { throw "Portable node not found at $nodeExe" }
$version = (& $nodeExe --version).TrimStart("v")
if ([version]$version -lt [version]"24.22.1") {
    throw "Portable Node $version is below the required 24.22.1"
}

New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
if (-not (Test-Path $backupFile)) { Get-RawUserPath | Set-Content -NoNewline $backupFile }

$pathChanged = $false
$raw = Get-RawUserPath
$parts = $raw -split ";" | Where-Object { $_ -ne "" }
if ($parts -notcontains $NodeDir) {
    try {
        $key.SetValue("Path", (($NodeDir) + ";" + ($parts -join ";")),
            [Microsoft.Win32.RegistryValueKind]::ExpandString)
        $pathChanged = $true
    } catch {
        # Some managed machines block writes to the user PATH key. The engine
        # still resolves the runtime through ZERO_NODE_DIR, and run.bat
        # prepends it for the app, so this is a warning, not a failure.
        $pathChanged = $false
        Write-Warning "Could not edit the user PATH ($($_.Exception.Message))."
        Write-Warning "ZERO_NODE_DIR is set; run.bat prepends it for the app."
    }
}

[Environment]::SetEnvironmentVariable("ZERO_NODE_DIR", $NodeDir, "User")

# Tell running Explorer/apps that the environment changed.
Add-Type -Namespace Win32 -Name NativeMethods -MemberDefinition @"
[DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Auto)]
public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint Msg, UIntPtr wParam,
    string lParam, uint fuFlags, uint uTimeout, out UIntPtr lpdwResult);
"@
$result = [UIntPtr]::Zero
[void][Win32.NativeMethods]::SendMessageTimeout(
    [IntPtr]0xffff, 0x1A, [UIntPtr]::Zero, "Environment", 2, 5000, [ref]$result)

Write-Host "Node $version activated for this user."
Write-Host "  ZERO_NODE_DIR = $NodeDir"
Write-Host "  PATH          = $(if ($pathChanged) { "$NodeDir;... (prepended)" } else { 'unchanged (policy); ZERO_NODE_DIR is used instead' })"
Write-Host "  Backup        = $backupFile"
Write-Host "Open a new terminal for 'node -v' to pick it up."
