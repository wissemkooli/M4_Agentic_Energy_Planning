# Open the backend and the frontend in two PowerShell windows, then open the browser.
$Scripts = $PSScriptRoot
Start-Process powershell -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Scripts "start-backend.ps1")
Start-Sleep -Seconds 4
Start-Process powershell -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Scripts "start-frontend.ps1")
Start-Sleep -Seconds 6
Start-Process "http://localhost:5173"
Write-Host "Backend: http://127.0.0.1:8000/docs   Frontend: http://localhost:5173"
