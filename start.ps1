param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendDir = Join-Path $ScriptDir "backend"
$VenvDir = Join-Path $BackendDir ".venv"
$PythonExe = Join-Path $VenvDir "Scripts\python.exe"

if (-not (Test-Path $VenvDir)) {
    Write-Host "Criando ambiente virtual..."
    python -m venv $VenvDir
}

Write-Host "Instalando dependencias..."
& $PythonExe -m pip install -q -r (Join-Path $BackendDir "requirements.txt")

Write-Host "Iniciando servidor em http://localhost:$Port ..."
Set-Location $BackendDir

# O backend Bluetooth do Windows (WinRT, usado pela lib `bleak`) pode
# derrubar o processo Python sem deixar nenhum traceback, especialmente
# depois de muitos ciclos de conexao/handshake de criptografia durante o
# polling. Isso e uma instabilidade conhecida da pilha BLE do Windows, nao
# um erro no codigo do app. Para o usuario nao precisar notar e reiniciar
# manualmente, reiniciamos o servidor sozinho sempre que ele cair; so pare
# com Ctrl+C.
while ($true) {
    & $PythonExe -m uvicorn app:app --host 0.0.0.0 --port $Port
    Write-Host ""
    Write-Host "Servidor encerrado (possivel instabilidade do Bluetooth no Windows). Reiniciando em 2s... (Ctrl+C para parar de vez)"
    Start-Sleep -Seconds 2
}
