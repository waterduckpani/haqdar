# Run as Administrator
Remove-NetFirewallRule -DisplayName "Whisper Server" -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName "Whisper Server" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Any
Write-Host "Firewall rule added for port 8000 (all profiles)."
