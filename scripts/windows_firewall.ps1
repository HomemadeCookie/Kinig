# Let the pod and phones reach the hub on Windows. Asks for admin if needed.
# Windows marks Kinig-Pod as a Public network, and Public blocks inbound by
# default. These rules allow the hub port (TCP 8000) and mDNS (UDP 5353) on
# every profile. Safe to run again.

$ErrorActionPreference = "Stop"

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Asking Windows for admin rights to add two firewall rules."
    $argList = "-ExecutionPolicy Bypass -File `"$PSCommandPath`""
    try {
        Start-Process powershell -Verb RunAs -ArgumentList $argList -Wait
    } catch {
        Write-Host "Admin was declined. The pod and phones may not reach the hub." -ForegroundColor Yellow
        Write-Host "Run scripts\windows_firewall.ps1 as administrator later." -ForegroundColor Yellow
    }
    return
}

$rules = @(
    @{ Name = "Kinig hub (TCP 8000)"; Protocol = "TCP"; Port = 8000 },
    @{ Name = "Kinig mDNS (UDP 5353)"; Protocol = "UDP"; Port = 5353 }
)
foreach ($rule in $rules) {
    if (Get-NetFirewallRule -DisplayName $rule.Name -ErrorAction SilentlyContinue) {
        Write-Host "Already open: $($rule.Name)"
        continue
    }
    New-NetFirewallRule -DisplayName $rule.Name -Direction Inbound -Action Allow `
        -Protocol $rule.Protocol -LocalPort $rule.Port -Profile Any | Out-Null
    Write-Host "Opened: $($rule.Name)"
}
Start-Sleep -Seconds 2
