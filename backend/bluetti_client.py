"""Camada de integração com o equipamento Bluetti via Bluetooth Low Energy.

A AC50P não tem uma classe dedicada em nenhuma biblioteca open-source
conhecida (bluetti_mqtt, bluetti-bt-lib, etc). Esses projetos cobrem a
família mais nova (AC60/AC70/AC180/AC200.../EB3A/EP5xx/EP6xx...) e usam dois
"protocolos" de transporte sobre o mesmo serviço BLE (characteristics
0xFF01/0xFF02): um mais antigo sem criptografia ("v1", usado por ex. na
EB3A) e um mais novo com handshake de chaves ("v2", usado nos modelos mais
recentes).

CONFIRMADO em hardware real: a AC50P fala o protocolo **v2 criptografado**,
não o v1 que seria a aposta óbvia pela geração do produto. Além disso, os
registradores de potência usados pela
AC60/AC70P (140=DC_OUT, 142=AC_OUT, 144=DC_IN, 146=AC_IN) retornam valores
coerentes também na AC50P — ver `ExperimentalPowerFieldsV2` abaixo.

A biblioteca `bluetti-bt-lib` tem um `recognize_device()` próprio que faz
esse handshake, mas rejeita a resposta da AC50P como "trash data" porque
valida o nome do tipo contra uma lista fixa de modelos conhecidos que não
inclui "AC50P" (só "AC50B", um modelo diferente). Por isso replicamos a
mesma lógica de handshake em `recognize_device_relaxed()`, com validação de
nome permissiva em vez da lista fixa.

Os comandos de controle (ligar/desligar saída CA/CC) em `ExperimentalControlFields`
usam os registradores da EB3A (3007/3008, protocolo v1) e por isso **não se
aplicam a esta unidade** (que fala v2) — ficam desabilitados automaticamente
na UI quando `iot_version != 1`. Não foram implementados comandos de
controle para v2 (endereços como 2011/2012 na AC60) porque, diferente de
leitura, uma escrita errada pode ter efeito real (ex.: cortar energia de
algo ligado na tomada) — ver aviso na UI antes de adicionar algo assim.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from bleak import BleakScanner
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from bluetti_bt_lib.base_devices import BaseDeviceV1, BaseDeviceV2
from bluetti_bt_lib.base_devices.bluetti_device import BluettiDevice
from bluetti_bt_lib.bluetooth.device_reader import DeviceReader, DeviceReaderConfig
from bluetti_bt_lib.bluetooth.device_writer import DeviceWriter, DeviceWriterConfig
from bluetti_bt_lib.fields import FieldName, SwitchField

logger = logging.getLogger("bluetti_client")

# Prefixos de nome BLE usados pelos equipamentos Bluetti (ver manifest do
# hassio-bluetti-bt, que cobre toda a linha atual de produtos).
BLUETTI_NAME_PREFIXES = (
    "AC", "AP", "EB", "EBOX", "EL", "EP", "Handsfree", "PBOX", "PR",
)

# Intervalo de polling: tempo de espera entre o fim de uma leitura e o
# início da próxima. Configurável via variável de ambiente
# BLUETTI_POLL_INTERVAL (segundos) e também ajustável em tempo real pela
# UI (POST /api/config), sem precisar reiniciar o servidor.
DEFAULT_POLL_INTERVAL_SECONDS = float(os.environ.get("BLUETTI_POLL_INTERVAL", 5))
MIN_POLL_INTERVAL_SECONDS = 2.0
MAX_POLL_INTERVAL_SECONDS = 600.0

# A `bluetti-bt-lib` traz um `recognize_device()` que faz exatamente este
# handshake, mas valida o nome do tipo de equipamento contra uma lista fixa
# de modelos conhecidos (AC2A, AC60, AC70, EB3A, ...). A AC50P não está
# nessa lista (só a AC50B, um modelo diferente), então mesmo que o
# equipamento responda corretamente com "AC50P<serial>", a lib descarta
# como "trash data" e a conexão nunca é reconhecida. Por isso replicamos a
# mesma lógica de handshake aqui, mas com uma validação de nome permissiva
# (qualquer string curta e imprimível), em vez da lista fixa.
_PLAUSIBLE_TYPE_RE = re.compile(r"^[A-Za-z0-9 _-]{2,24}$")


@dataclass
class RecognizedDevice:
    type_name: str
    iot_version: int
    encrypted: bool
    serial: Any = None

    @property
    def full_name(self) -> str:
        return f"{self.type_name}{self.serial if self.serial is not None else ''}"


async def recognize_device_relaxed(
    mac: str,
    future_builder_method: Callable[[], asyncio.Future[Any]],
) -> Optional[RecognizedDevice]:
    candidates: list[BluettiDevice] = [BaseDeviceV2(), BaseDeviceV1()]

    for bluetti_device in candidates:
        device_readers = [
            DeviceReader(
                mac, bluetti_device, future_builder_method,
                DeviceReaderConfig(timeout=15, use_encryption=True),
            ),
            DeviceReader(
                mac, bluetti_device, future_builder_method,
                DeviceReaderConfig(timeout=8),
            ),
        ]

        for device_reader in device_readers:
            data = await device_reader.read(bluetti_device.get_device_type_registers())
            if data is None:
                continue

            type_data = data.get(FieldName.DEVICE_TYPE.value)
            if not isinstance(type_data, str) or type_data == "":
                continue

            if _PLAUSIBLE_TYPE_RE.match(type_data) is None:
                logger.warning("Tipo de dispositivo com dados ilegíveis: %r", type_data)
                continue

            sn_result = await device_reader.read(bluetti_device.get_device_sn_registers())
            sn_data = sn_result.get(FieldName.DEVICE_SN.value) if sn_result else None
            if not isinstance(sn_data, int):
                sn_data = None

            return RecognizedDevice(
                type_name=type_data,
                iot_version=bluetti_device.get_iot_version(),
                encrypted=device_reader.config.use_encryption,
                serial=sn_data,
            )

    return None


class ExperimentalControlFields(BaseDeviceV1):
    """BaseDeviceV1 genérico + registradores de controle "best effort".

    Os endereços 3007 (CTRL_AC) e 3008 (CTRL_DC) são os usados pela EB3A.
    Mantido separado da classe de leitura para deixar claro que isso é uma
    suposição, não um dado confirmado para a AC50P.
    """

    def __init__(self):
        super().__init__([
            SwitchField(FieldName.CTRL_AC, 3007),
            SwitchField(FieldName.CTRL_DC, 3008),
        ])


class ExperimentalPowerFieldsV2(BaseDeviceV2):
    """BaseDeviceV2 genérico + registradores de potência.

    Endereços 140/142/144/146, originalmente observados na AC60/AC70P.
    CONFIRMADO funcionando na AC50P com hardware real (teste controlado:
    unidade desligada da tomada, só descarregando por uma carga na saída
    CA): AC_OUTPUT_POWER (142) mostrou a potência real da carga, enquanto
    AC_INPUT_POWER (146) foi corretamente para 0 — ou seja, é mesmo uma
    medição independente de entrada da tomada, não um eco da saída. Um
    valor igual entre os dois (visto em testes anteriores) é passagem de
    energia real: a unidade estava ligada na tomada e descarregando ao
    mesmo tempo.

    DC_OUTPUT_POWER (140): correto. DC_INPUT_POWER (144): só testado com a
    unidade descarregando (deu 0, consistente); carregando via solar/carro
    ainda não foi testado.

    Leitura apenas — sem risco de escrita indevida no equipamento.
    """

    def __init__(self):
        from bluetti_bt_lib.fields import UIntField

        super().__init__([
            UIntField(FieldName.DC_OUTPUT_POWER, 140),
            UIntField(FieldName.AC_OUTPUT_POWER, 142),
            UIntField(FieldName.DC_INPUT_POWER, 144),
            UIntField(FieldName.AC_INPUT_POWER, 146),
        ])


@dataclass
class DeviceStatus:
    connected: bool = False
    connecting: bool = False
    address: Optional[str] = None
    name: Optional[str] = None
    iot_version: Optional[int] = None
    encrypted: bool = False
    battery_soc: Optional[int] = None
    ac_output_power: Optional[int] = None
    dc_output_power: Optional[int] = None
    ac_input_power: Optional[int] = None
    dc_input_power: Optional[int] = None
    last_update: Optional[float] = None
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "connecting": self.connecting,
            "address": self.address,
            "name": self.name,
            "iot_version": self.iot_version,
            "encrypted": self.encrypted,
            "battery_soc": self.battery_soc,
            "ac_output_power": self.ac_output_power,
            "dc_output_power": self.dc_output_power,
            "ac_input_power": self.ac_input_power,
            "dc_input_power": self.dc_input_power,
            "last_update": self.last_update,
            "error": self.error,
            "control_supported": self.iot_version == 1,
        }


Listener = Callable[[DeviceStatus], Awaitable[None]]


class BluettiManager:
    """Mantém o estado de conexão e o loop de polling de um único equipamento."""

    def __init__(self):
        self.status = DeviceStatus()
        self.device: Optional[BluettiDevice] = None
        self.poll_interval = DEFAULT_POLL_INTERVAL_SECONDS
        self._poll_task: Optional[asyncio.Task] = None
        # Cancelamento cooperativo do polling: `Task.cancel()` não é
        # confiável aqui porque `DeviceReader.read()` (na bluetti-bt-lib)
        # tem um `except BaseException` interno que engole o
        # CancelledError de uma leitura em andamento (até ~45s em modo
        # criptografado). Isso faz a task nunca terminar de verdade e
        # `await self._poll_task` travaria para sempre no disconnect. Em
        # vez disso, o loop checa esta flag e para sozinho na próxima
        # oportunidade, sem o backend precisar esperar por ele.
        self._stop_polling = False
        # Incrementado em toda chamada a connect()/disconnect(). Permite a
        # um connect() em andamento (que pode ficar até ~45s esperando o
        # handshake de reconhecimento) notar que foi "abandonado" por um
        # disconnect() ou por outro connect() mais recente, e desistir sem
        # pisar no estado da conexão atual.
        self._generation = 0
        self._listeners: list[Listener] = []
        self._lock = asyncio.Lock()
        # No Windows, o backend BLE (WinRT) é instável com operações
        # concorrentes no rádio (ex.: escanear enquanto se faz polling de
        # um equipamento conectado pode derrubar o processo sem traceback
        # nenhum). Este lock serializa TODA operação de rádio (scan,
        # handshake de conexão, polling e escrita de controle) para que
        # só exista uma de cada vez.
        self._ble_lock = asyncio.Lock()

    async def set_poll_interval(self, seconds: float) -> float:
        """Define o intervalo de polling (em segundos), com limites de
        sanidade. Entra em vigor no próximo ciclo do loop — não precisa
        reconectar nem reiniciar o servidor."""
        clamped = max(MIN_POLL_INTERVAL_SECONDS, min(MAX_POLL_INTERVAL_SECONDS, float(seconds)))
        self.poll_interval = clamped
        await self._notify()
        return clamped

    def add_listener(self, cb: Listener) -> None:
        self._listeners.append(cb)

    def remove_listener(self, cb: Listener) -> None:
        if cb in self._listeners:
            self._listeners.remove(cb)

    async def _notify(self) -> None:
        for cb in list(self._listeners):
            try:
                await cb(self.status)
            except Exception:
                logger.exception("Erro ao notificar listener")

    async def scan(self, timeout: float = 8.0) -> dict[str, list[dict[str, str]]]:
        """Escaneia dispositivos BLE próximos.

        No Windows, o nome de um dispositivo às vezes só é capturado em
        parte dos pacotes de anúncio recebidos durante o scan, então um
        único `BleakScanner.discover()` pode retornar o dispositivo certo
        com `name=None`. Por isso usamos um `detection_callback` que vai
        mesclando o melhor nome visto para cada endereço ao longo de todo
        o período de escaneamento, e devolvemos também os dispositivos
        sem nome reconhecido como Bluetti, para o usuário poder tentar
        conectar manualmente pelo endereço.
        """
        if self._ble_lock.locked():
            raise RuntimeError(
                "Já existe uma operação Bluetooth em andamento (scan/conexão). Aguarde terminar."
            )

        async with self._ble_lock:
            seen: dict[str, dict[str, str | None]] = {}

            def on_detect(device, advertisement_data) -> None:
                name = device.name or advertisement_data.local_name
                current = seen.get(device.address)
                if current is None:
                    seen[device.address] = {"address": device.address, "name": name}
                elif not current["name"] and name:
                    current["name"] = name

            scanner = BleakScanner(detection_callback=on_detect)
            await scanner.start()
            try:
                await asyncio.sleep(timeout)
            finally:
                await scanner.stop()

        bluetti: list[dict[str, str]] = []
        others: list[dict[str, str]] = []
        for info in seen.values():
            name = info["name"] or ""
            entry = {"address": info["address"], "name": name or "(sem nome)"}
            if name.startswith(BLUETTI_NAME_PREFIXES):
                bluetti.append(entry)
            else:
                others.append(entry)

        return {"bluetti": bluetti, "others": others}

    async def connect(self, address: str) -> DeviceStatus:
        if self._lock.locked():
            raise RuntimeError(
                "Já existe uma tentativa de conexão em andamento. Aguarde ela terminar "
                "(pode levar até ~45s) antes de tentar de novo."
            )

        async with self._lock:
            self._generation += 1
            my_generation = self._generation

            self._reset_state()
            self.status = DeviceStatus(connecting=True, address=address)
            await self._notify()

            loop = asyncio.get_event_loop()
            try:
                async with self._ble_lock:
                    result = await recognize_device_relaxed(address, loop.create_future)
            except Exception as exc:
                logger.exception("Falha no reconhecimento do dispositivo")
                if my_generation == self._generation:
                    self.status = DeviceStatus(error=f"Erro ao identificar o equipamento: {exc}")
                    await self._notify()
                raise

            # Um disconnect() (ou outro connect()) pode ter chegado enquanto
            # esperávamos o handshake (até ~45s). Se for o caso, esse
            # resultado já não importa — não sobrescreve o estado atual.
            if my_generation != self._generation:
                return self.status

            if result is None:
                self.status = DeviceStatus(
                    error=(
                        "Não foi possível identificar o equipamento por Bluetooth. "
                        "Verifique se está ligado, próximo e sem outro app "
                        "(ex.: Bluetti app) conectado a ele."
                    )
                )
                await self._notify()
                raise RuntimeError("device not recognized")

            if result.iot_version == 1:
                self.device = ExperimentalControlFields()
            else:
                self.device = ExperimentalPowerFieldsV2()

            self.status = DeviceStatus(
                connected=True,
                address=address,
                name=result.full_name,
                iot_version=result.iot_version,
                encrypted=result.encrypted,
            )
            await self._notify()

            self._stop_polling = False
            self._poll_task = asyncio.create_task(self._poll_loop())
            return self.status

    async def _poll_loop(self) -> None:
        loop = asyncio.get_event_loop()
        while not self._stop_polling:
            try:
                reader = DeviceReader(
                    self.status.address,
                    self.device,
                    loop.create_future,
                    # Timeout alto: a leitura percorre vários registradores em
                    # sequência (um round-trip BLE cada) e, com criptografia v2,
                    # isso pode facilmente passar de 15s e descartar a leitura
                    # inteira mesmo que a maioria dos campos tenha respondido.
                    DeviceReaderConfig(timeout=45, use_encryption=self.status.encrypted),
                )
                async with self._ble_lock:
                    data = await reader.read()

                # Pode ter sido pedido para parar enquanto a leitura (até
                # 45s) estava em andamento: não toca mais em self.status,
                # que já pode pertencer a uma conexão nova.
                if self._stop_polling:
                    break

                if data:
                    self.status.battery_soc = data.get(FieldName.BATTERY_SOC.value)
                    self.status.ac_output_power = data.get(FieldName.AC_OUTPUT_POWER.value)
                    self.status.dc_output_power = data.get(FieldName.DC_OUTPUT_POWER.value)
                    self.status.ac_input_power = data.get(FieldName.AC_INPUT_POWER.value)
                    self.status.dc_input_power = data.get(FieldName.DC_INPUT_POWER.value)
                    self.status.last_update = time.time()
                    self.status.error = None
                else:
                    self.status.error = "Sem resposta do equipamento (timeout)."
                await self._notify()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Erro no polling")
                self.status.error = str(exc)
                await self._notify()

            if self._stop_polling:
                break
            await asyncio.sleep(self.poll_interval)

    async def disconnect(self) -> None:
        # Propositalmente NÃO usa `self._lock`: se houver um connect() ainda
        # tentando o handshake (até ~45s), queremos poder desistir dele na
        # hora, não esperar a vez. O connect() em andamento vai notar que a
        # "generation" mudou e desistir sozinho sem pisar nesse reset.
        self._generation += 1
        self._reset_state()
        await self._notify()

    def _reset_state(self) -> None:
        # Só sinaliza a flag e descarta a referência à task — não esperamos
        # ela terminar de verdade (ver comentário em __init__ sobre o
        # CancelledError sendo engolido pela lib em leituras longas). Isso
        # mantém o endpoint de desconectar instantâneo mesmo com uma leitura
        # de até 45s em andamento.
        self._stop_polling = True
        if self._poll_task is not None:
            self._poll_task.cancel()
            self._poll_task = None
        self.device = None
        self.status = DeviceStatus()

    async def set_output(self, channel: str, on: bool) -> None:
        """Comando experimental: liga/desliga saída CA ou CC.

        Só disponível quando o equipamento respondeu ao protocolo v1
        (mesma família/geração da EB3A). Não há garantia de que os
        registradores 3007/3008 tenham o mesmo efeito na AC50P.
        """
        if self.device is None or not self.status.connected:
            raise RuntimeError("Não conectado a nenhum equipamento.")
        if self.status.iot_version != 1:
            raise RuntimeError(
                "Controle experimental só está implementado para o protocolo v1."
            )

        field_name = (
            FieldName.CTRL_AC.value if channel == "ac" else FieldName.CTRL_DC.value
        )

        async with self._ble_lock:
            ble_device = await BleakScanner.find_device_by_address(self.status.address, timeout=5)
            if ble_device is None:
                raise RuntimeError("Equipamento não encontrado via Bluetooth (fora de alcance?).")

            client = await establish_connection(
                BleakClientWithServiceCache, ble_device, ble_device.name or "bluetti",
            )
            try:
                writer = DeviceWriter(
                    client, self.device, DeviceWriterConfig(timeout=15, use_encryption=False),
                )
                await writer.write(field_name, on)
            finally:
                await client.disconnect()
