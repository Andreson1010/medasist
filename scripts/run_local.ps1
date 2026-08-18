<#
.SYNOPSIS
    Sobe a API (uvicorn) e a UI (Streamlit) localmente, gerenciando o ciclo de vida dos processos.

.DESCRIPTION
    Inicia a API MedAssist e a UI Streamlit como processos em background usando
    arquivos de log exclusivos por execução. Antes de iniciar, encerra qualquer
    processo que já esteja escutando nas portas configuradas (evita conflito de
    porta e trava de arquivos de log obsoletos). No encerramento (Ctrl+C),
    mata os processos filhos.

.EXAMPLE
    .\scripts\run_local.ps1            # sobe API + UI
    .\scripts\run_local.ps1 -NoUI      # sobe apenas a API
#>
param(
    [switch]$NoUI,
    [int]$ApiPort = 8000,
    [int]$UiPort = 8501
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Root ".venv\Scripts\python.exe"
$LogDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

function Stop-PortProcess {
    param([int]$Port)
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        $p = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
        if ($p) {
            Write-Host "Encerrando processo $($p.ProcessName) (PID $($p.Id)) na porta $Port..."
            Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        }
    }
}

function Wait-Port {
    param([int]$Port, [int]$TimeoutSec = 60)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
            return $true
        }
        Start-Sleep -Seconds 1
    }
    return $false
}

Stop-PortProcess -Port $ApiPort
if (-not $NoUI) { Stop-PortProcess -Port $UiPort }

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$apiOut = Join-Path $LogDir "api_$stamp.out.log"
$apiErr = Join-Path $LogDir "api_$stamp.err.log"

Write-Host "Iniciando API em http://localhost:$ApiPort (logs: $apiOut / $apiErr)..."
$api = Start-Process -FilePath $Py `
    -ArgumentList "-m", "uvicorn", "src.medasist.api.main:app", "--host", "127.0.0.1", "--port", "$ApiPort" `
    -WorkingDirectory $Root `
    -RedirectStandardOutput $apiOut `
    -RedirectStandardError $apiErr `
    -PassThru

if (-not (Wait-Port -Port $ApiPort)) {
    Write-Error "API não respondeu na porta $ApiPort. Veja $apiErr"
    exit 1
}
Write-Host "API OK (PID $($api.Id))."

if (-not $NoUI) {
    $uiOut = Join-Path $LogDir "ui_$stamp.out.log"
    $uiErr = Join-Path $LogDir "ui_$stamp.err.log"
    Write-Host "Iniciando UI em http://localhost:$UiPort (logs: $uiOut / $uiErr)..."
    $ui = Start-Process -FilePath $Py `
        -ArgumentList "-m", "streamlit", "run", "src/medasist/ui/app.py", "--server.headless", "true", "--server.port", "$UiPort" `
        -WorkingDirectory $Root `
        -RedirectStandardOutput $uiOut `
        -RedirectStandardError $uiErr `
        -PassThru

    if (-not (Wait-Port -Port $UiPort)) {
        Write-Warning "UI não respondeu na porta $UiPort. Veja $uiErr"
    } else {
        Write-Host "UI OK (PID $($ui.Id))."
    }
}

Write-Host ""
Write-Host "Pressione Ctrl+C para encerrar API e UI."
try {
    while ($true) { Start-Sleep -Seconds 1 }
} finally {
    foreach ($proc in @($api) + @($ui)) {
        if ($proc -and -not $proc.HasExited) {
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        }
    }
    Write-Host "Processos encerrados."
}