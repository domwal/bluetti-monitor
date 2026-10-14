#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
VENV_DIR="$BACKEND_DIR/.venv"
PORT="${1:-8000}"

if [ ! -d "$VENV_DIR" ]; then
    echo "Criando ambiente virtual..."
    python3 -m venv "$VENV_DIR" 2>/dev/null || python -m venv "$VENV_DIR"
fi

if [ -f "$VENV_DIR/bin/python" ]; then
    PYTHON_EXE="$VENV_DIR/bin/python"
else
    PYTHON_EXE="$VENV_DIR/Scripts/python.exe"
fi

echo "Instalando dependencias..."
"$PYTHON_EXE" -m pip install -q -r "$BACKEND_DIR/requirements.txt"

echo "Iniciando servidor em http://localhost:$PORT ..."
cd "$BACKEND_DIR"

# O backend Bluetooth do Windows (WinRT, usado pela lib `bleak`) pode
# derrubar o processo Python sem deixar nenhum traceback, especialmente
# depois de muitos ciclos de conexao/handshake de criptografia durante o
# polling. Isso e uma instabilidade conhecida da pilha BLE do Windows, nao
# um erro no codigo do app. Para o usuario nao precisar notar e reiniciar
# manualmente, reiniciamos o servidor sozinho sempre que ele cair; so pare
# com Ctrl+C.
while true; do
    "$PYTHON_EXE" -m uvicorn app:app --host 0.0.0.0 --port "$PORT" || true
    echo ""
    echo "Servidor encerrado (possivel instabilidade do Bluetooth no Windows). Reiniciando em 2s... (Ctrl+C para parar de vez)"
    sleep 2
done
