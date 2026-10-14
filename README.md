# Bluetti AC50P — App Web (Python + HTML/JS)

App local para monitorar (e, de forma experimental, controlar) uma estação
Bluetti AC50P via Bluetooth Low Energy, com um backend em Python
(FastAPI + [bleak](https://github.com/hbldh/bleak)) e um front-end estático
em HTML/CSS/JS puro servido pelo mesmo processo.

## Compatibilidade com a AC50P

A AC50P **não tem suporte dedicado** em nenhuma biblioteca open-source
conhecida (`bluetti_mqtt`, `bluetti-bt-lib` usada pela integração do Home
Assistant, etc.) — essas bibliotecas cobrem a família mais nova (AC60,
AC70, AC180, AC200..., AC300/500, EB3A, EP500/600...), e a lista de
modelos reconhecidos do `bluetti-bt-lib` nem inclui "AC50P" (só "AC50B",
um modelo diferente).

**Testado em hardware real e confirmado funcionando:**

- A AC50P fala o protocolo **v2 criptografado** (handshake de troca de
  chaves) — não o v1 sem criptografia que seria a aposta óbvia pela idade
  do produto.
- A biblioteca `bluetti-bt-lib` tenta identificar o equipamento com esse
  mesmo handshake, mas descarta a resposta da AC50P como "trash data" por
  validar o nome do tipo contra a lista fixa de modelos (que não inclui
  AC50P). Este app contorna isso com um reconhecimento próprio
  (`recognize_device_relaxed` em `bluetti_client.py`) que aceita qualquer
  nome de tipo plausível, não só os da lista.
- Os registradores de **bateria** (102) e **potência CA/CC de
  entrada/saída** (140/142/144/146 — originalmente mapeados para a
  AC60/AC70P) retornam valores corretos também na AC50P. Confirmado com
  teste controlado: unidade fora da tomada, descarregando por uma carga
  na saída CA — "Saída CA" mostrou a potência real da carga e "Entrada
  CA" corretamente foi para 0 (ou seja, é mesmo uma medição independente,
  não um eco da saída).

**Não confirmado / não implementado:**

- Os dois toggles de controle (ligar/desligar saída CA/CC) na UI usam os
  endereços de registrador da EB3A (3007/3008), que são do protocolo
  **v1**. Como a AC50P fala v2, esses endereços não valem para ela — por
  isso o painel de controle fica **desabilitado automaticamente** depois
  de conectar (`control_supported: false`).
- Não implementamos escrita para o protocolo v2 (ex.: os endereços
  2011/2012 usados pela AC60 para o mesmo fim). Diferente de leitura, uma
  escrita errada pode ter efeito real no equipamento (ex.: cortar energia
  de algo ligado na tomada), então isso não foi adicionado sem antes
  confirmar os endereços certos para a AC50P.

Se quiser ir além (controle real), o caminho é capturar o tráfego BLE do
app oficial da Bluetti enquanto liga/desliga a saída CA/CC (ex.: com
nRF Connect, ou HCI snoop no Android) para descobrir os registradores de
escrita corretos da AC50P no protocolo v2.

## Requisitos

- Python 3.10+
- Um adaptador Bluetooth na máquina que vai rodar o backend (é ele que
  fala com a AC50P — o navegador não usa Bluetooth diretamente).
- Windows: certifique-se de que o Bluetooth está ativado no sistema.

## Instalação

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/Mac
pip install -r requirements.txt
```

## Executar

```bash
cd backend
python app.py
```

Abra **http://localhost:8000** no navegador.

## Docker

**Só funciona com o host rodando Linux** (ex. Raspberry Pi, servidor
Linux, VM Linux) — o backend precisa falar direto com o BlueZ do sistema
via D-Bus para acessar o Bluetooth. **Docker Desktop no Windows ou Mac não
tem passthrough de BLE**, então o container nunca veria o adaptador
Bluetooth nessas plataformas; para desenvolvimento no Windows, use a
instalação local (seção acima) em vez de Docker.

No host Linux, o BlueZ (`bluetoothd`) precisa estar rodando normalmente
fora do container — o compose só monta o socket do D-Bus pra falar com
ele:

```bash
docker compose up -d --build
```

O `docker-compose.yml` usa `network_mode: host` + `privileged: true` +
o socket `/var/run/dbus` montado — é a receita padrão para BLE em
container (a mesma usada por outras integrações Bluetooth, como as do
Home Assistant). Com rede em modo host, a porta já fica em
`http://<ip-do-host>:8000` sem precisar mapear `ports:`.

O intervalo de polling pode ser ajustado via variável de ambiente no
próprio `docker-compose.yml` (`BLUETTI_POLL_INTERVAL`) ou depois, ao vivo,
pela própria tela (ver seção de Uso).

## Uso

1. **Escanear Bluetooth** — lista equipamentos próximos cujo nome começa
   com os prefixos usados pela Bluetti (`AC...`, `EB...`, `EP...` etc.).
   Se a AC50P não aparecer, use o endereço MAC manualmente (visível, por
   ex., pelo app oficial da Bluetti ou por um scanner BLE como o
   nRF Connect).
2. **Conectar** — o backend tenta o handshake e começa a atualizar
   bateria, potência de entrada/saída CA/CC (via WebSocket). Cada leitura
   pode levar até ~45s (vários registradores em sequência sob
   criptografia v2), então o painel demora um pouco para preencher após
   conectar.
3. **Controle (experimental)** — fica desabilitado na AC50P, pois ela fala
   o protocolo v2 e os comandos implementados são para o v1 (ver seção
   acima).

## Estrutura

```
backend/
  app.py              # servidor FastAPI (REST + WebSocket + arquivos estáticos)
  bluetti_client.py   # integração BLE com a AC50P (scan, conexão, polling, controle)
  requirements.txt
frontend/
  index.html
  style.css
  app.js
start.ps1 / start.sh  # sobem o servidor localmente (Windows/Linux-Mac)
Dockerfile / docker-compose.yml  # build/execução em container (só host Linux)
```
