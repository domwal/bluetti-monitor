const el = (id) => document.getElementById(id);

const connectCard = el("connect-card");
const connIndicator = el("conn-indicator");
const connText = el("conn-text");
const btnScan = el("btn-scan");
const scanStatus = el("scan-status");
const deviceList = el("device-list");
const othersWrap = el("others-wrap");
const othersList = el("others-list");
const manualAddress = el("manual-address");
const btnConnectManual = el("btn-connect-manual");
const connectError = el("connect-error");

const statusEmpty = el("status-empty");
const statusGrid = el("status-grid");
const statusError = el("status-error");
const statusUpdated = el("status-updated");
const btnDisconnect = el("btn-disconnect");

const metricSoc = el("metric-soc");
const batteryFill = el("battery-fill");
const metricAcOut = el("metric-ac-out");
const metricDcOut = el("metric-dc-out");
const metricAcIn = el("metric-ac-in");
const metricDcIn = el("metric-dc-in");
const metricProto = el("metric-proto");

const pollIntervalInput = el("poll-interval-input");
const btnSaveInterval = el("btn-save-interval");
const pollIntervalStatus = el("poll-interval-status");

const controlPanel = el("control-panel");
const controlDisabled = el("control-disabled");
const controlError = el("control-error");
const toggleAc = el("toggle-ac");
const toggleDc = el("toggle-dc");

let ws = null;
let suppressToggleEvents = false;
let autoReconnectTried = false;
// Só tenta reconectar sozinho se a queda aconteceu DURANTE a sessão (ex.:
// o servidor caiu e reiniciou). Numa carga de página normal, começando
// desconectado, não deve tentar nada sem o usuário clicar em algo.
let hasConnectedThisSession = false;

const LAST_ADDRESS_KEY = "bluetti_last_address";

function rememberAddress(address) {
  try { localStorage.setItem(LAST_ADDRESS_KEY, address); } catch (e) { /* ignore */ }
}

function getRememberedAddress() {
  try { return localStorage.getItem(LAST_ADDRESS_KEY); } catch (e) { return null; }
}

function fmtWatts(value) {
  if (value === null || value === undefined) return "—";
  return `${value} W`;
}

function setIndicator(status) {
  connIndicator.classList.remove("badge--off", "badge--connecting", "badge--on");
  if (status.connected) {
    connText.textContent = status.name ? `conectado: ${status.name}` : "conectado";
    connIndicator.classList.add("badge--on");
  } else if (status.connecting) {
    connText.textContent = "conectando...";
    connIndicator.classList.add("badge--connecting");
  } else {
    connText.textContent = "desconectado";
    connIndicator.classList.add("badge--off");
  }
}

function renderStatus(status) {
  setIndicator(status);

  connectCard.classList.toggle("hidden", status.connected);

  if (status.poll_interval !== undefined && document.activeElement !== pollIntervalInput) {
    pollIntervalInput.value = status.poll_interval;
  }

  if (status.connected) {
    autoReconnectTried = false;
    hasConnectedThisSession = true;
    statusEmpty.classList.add("hidden");
    statusGrid.classList.remove("hidden");
    btnDisconnect.classList.remove("hidden");

    const soc = status.battery_soc;
    metricSoc.textContent = soc === null || soc === undefined ? "—" : `${soc}%`;
    batteryFill.style.width = `${soc ?? 0}%`;
    batteryFill.classList.remove("level--low", "level--mid");
    if (soc !== null && soc !== undefined) {
      if (soc <= 20) batteryFill.classList.add("level--low");
      else if (soc <= 50) batteryFill.classList.add("level--mid");
    }
    metricAcOut.textContent = fmtWatts(status.ac_output_power);
    metricDcOut.textContent = fmtWatts(status.dc_output_power);
    metricAcIn.textContent = fmtWatts(status.ac_input_power);
    metricDcIn.textContent = fmtWatts(status.dc_input_power);
    metricProto.textContent = status.iot_version
      ? `v${status.iot_version}${status.encrypted ? " (criptografado)" : ""}`
      : "—";

    if (status.last_update) {
      const d = new Date(status.last_update * 1000);
      statusUpdated.textContent = `Última leitura: ${d.toLocaleTimeString()}`;
    }
  } else {
    statusEmpty.classList.remove("hidden");
    statusGrid.classList.add("hidden");
    btnDisconnect.classList.add("hidden");
    statusUpdated.textContent = "";

    // O backend Bluetooth do Windows pode derrubar o servidor sem aviso
    // (ver README); o script de start reinicia o processo sozinho, mas a
    // conexão com o equipamento se perde. Tentamos reconectar uma vez,
    // automaticamente, ao último endereço usado, sem precisar que o
    // usuário clique de novo.
    if (hasConnectedThisSession && !status.connecting && !autoReconnectTried) {
      const saved = getRememberedAddress();
      if (saved) {
        autoReconnectTried = true;
        setTimeout(() => doConnect(saved), 3000);
      }
    }
  }

  statusError.textContent = status.error || "";

  if (status.connected && status.control_supported) {
    controlPanel.classList.remove("hidden");
    controlDisabled.textContent = "";
  } else {
    controlPanel.classList.add("hidden");
    controlDisabled.textContent = status.connected
      ? "Controle não disponível: o equipamento respondeu usando o protocolo v2, para o qual o comando experimental não foi implementado."
      : "";
  }
}

async function api(path, options) {
  const res = await fetch(path, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || `Erro HTTP ${res.status}`);
  }
  return data;
}

function connectWebSocket() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (ev) => {
    const status = JSON.parse(ev.data);
    renderStatus(status);
  };
  ws.onclose = () => {
    setTimeout(connectWebSocket, 2000);
  };
  ws.onerror = () => ws.close();
}

async function refreshStatusOnce() {
  try {
    const status = await api("/api/status");
    renderStatus(status);
  } catch (e) {
    // ignore, websocket vai atualizar
  }
}

function renderDeviceList(listEl, devices) {
  listEl.innerHTML = "";
  for (const dev of devices) {
    const li = document.createElement("li");
    li.innerHTML = `<span><span class="device-name">${dev.name}</span><span class="device-address">${dev.address}</span></span>`;
    const btn = document.createElement("button");
    btn.textContent = "Conectar";
    btn.addEventListener("click", () => doConnect(dev.address));
    li.appendChild(btn);
    listEl.appendChild(li);
  }
}

btnScan.addEventListener("click", async () => {
  deviceList.innerHTML = "";
  othersList.innerHTML = "";
  othersWrap.classList.add("hidden");
  scanStatus.innerHTML = '<i class="spinner"></i>Escaneando (alguns segundos)...';
  btnScan.disabled = true;
  try {
    const { bluetti, others } = await api("/api/scan");
    scanStatus.textContent = bluetti.length
      ? `${bluetti.length} equipamento(s) Bluetti encontrado(s).`
      : "Nenhum equipamento Bluetti encontrado nas proximidades.";
    renderDeviceList(deviceList, bluetti);
    if (others.length) {
      renderDeviceList(othersList, others);
      othersWrap.classList.remove("hidden");
    }
  } catch (e) {
    scanStatus.textContent = `Erro ao escanear: ${e.message}`;
  } finally {
    btnScan.disabled = false;
  }
});

btnConnectManual.addEventListener("click", () => {
  const addr = manualAddress.value.trim();
  if (addr) doConnect(addr);
});

function setConnectButtonsDisabled(disabled) {
  btnConnectManual.disabled = disabled;
  document.querySelectorAll("#device-list button, #others-list button").forEach((b) => {
    b.disabled = disabled;
  });
}

let connectInFlight = false;

async function doConnect(address) {
  if (connectInFlight) return;
  connectInFlight = true;
  connectError.textContent = "";
  setConnectButtonsDisabled(true);
  try {
    const status = await api("/api/connect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ address }),
    });
    rememberAddress(address);
    renderStatus(status);
  } catch (e) {
    connectError.textContent = `Falha ao conectar: ${e.message}`;
  } finally {
    connectInFlight = false;
    setConnectButtonsDisabled(false);
  }
}

btnDisconnect.addEventListener("click", async () => {
  // Desconexão pedida pelo usuário: não deve disparar a reconexão automática.
  autoReconnectTried = true;
  await api("/api/disconnect", { method: "POST" });
});

btnSaveInterval.addEventListener("click", async () => {
  const value = Number(pollIntervalInput.value);
  if (!Number.isFinite(value) || value <= 0) {
    pollIntervalStatus.textContent = "Valor inválido.";
    return;
  }
  pollIntervalStatus.textContent = "Salvando...";
  try {
    const result = await api("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ poll_interval: value }),
    });
    pollIntervalInput.value = result.poll_interval;
    pollIntervalStatus.textContent = "Salvo.";
    setTimeout(() => { pollIntervalStatus.textContent = ""; }, 2000);
  } catch (e) {
    pollIntervalStatus.textContent = `Erro: ${e.message}`;
  }
});

(async () => {
  try {
    const config = await api("/api/config");
    pollIntervalInput.value = config.poll_interval;
  } catch (e) { /* status/websocket também preenche isso */ }
})();

async function doControl(channel, on, toggleInput) {
  controlError.textContent = "";
  toggleInput.disabled = true;
  try {
    await api("/api/control", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ channel, on }),
    });
  } catch (e) {
    controlError.textContent = `Falha no comando: ${e.message}`;
    suppressToggleEvents = true;
    toggleInput.checked = !on;
    suppressToggleEvents = false;
  } finally {
    toggleInput.disabled = false;
  }
}

toggleAc.addEventListener("change", () => {
  if (suppressToggleEvents) return;
  const confirmed = confirm(
    "Comando experimental (não confirmado para a AC50P). Continuar?"
  );
  if (!confirmed) {
    suppressToggleEvents = true;
    toggleAc.checked = !toggleAc.checked;
    suppressToggleEvents = false;
    return;
  }
  doControl("ac", toggleAc.checked, toggleAc);
});

toggleDc.addEventListener("change", () => {
  if (suppressToggleEvents) return;
  const confirmed = confirm(
    "Comando experimental (não confirmado para a AC50P). Continuar?"
  );
  if (!confirmed) {
    suppressToggleEvents = true;
    toggleDc.checked = !toggleDc.checked;
    suppressToggleEvents = false;
    return;
  }
  doControl("dc", toggleDc.checked, toggleDc);
});

renderStatus({ connected: false, connecting: false });
refreshStatusOnce();
connectWebSocket();
