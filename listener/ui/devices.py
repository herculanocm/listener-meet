"""U-043 (F5.2): o combo do microfone acompanha os dispositivos do sistema sem reiniciar o app. O "Antes de gravar" e a
página do microfone do assistente chamam `refresh_mics` a cada `REFRESH_MS`: se o Windows ganhou ou perdeu um dispositivo,
o medidor é fechado (o PortAudio só reinicia sem stream aberto), a lista é relida e o combo é refeito mantendo a escolha
pelo id (ou o padrão, com aviso); quem chama reabre o medidor. F5.3 (U-045, pergunta 11): no "Antes de gravar" o combo
começa com "Padrão do Windows (acompanha)" (`follow`), que segue o padrão do Windows também durante a gravação (A-20).
D-067: quando a lista não pôde ser relida (outro som do app segura o PortAudio) e o medidor do "acompanha" ficou num
microfone que já não é o padrão, ele reabre no padrão de agora, se este já está na lista (a plataforma abre pelo id)."""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, NamedTuple

from ..log import get_logger

if TYPE_CHECKING:
    from PySide6.QtWidgets import QComboBox

    from ..platform.base import AudioDevice, CaptureHandle, Platform

log = get_logger("ui.devices")

REFRESH_MS = 2000
FOLLOW_ID = ""                      # o dado do item "acompanha" (o microfone pedido é None)
FOLLOW_TEXT = "Padrão do Windows (acompanha)"


class MicRefresh(NamedTuple):
    devices: list[AudioDevice]
    note: str                       # "novo: Fone XYZ", "o microfone escolhido saiu; usando o padrão" ou vazio
    reopen: bool                    # o medidor foi fechado: quem chama o reabre


def refresh_mics(platform: Platform, combo: QComboBox, devices: list[AudioDevice], *, meter_open: bool,
                 close_meter: Callable[[], None], follow: bool = False, meter: CaptureHandle | None = None) -> MicRefresh | None:
    """None quando nada mudou (o medidor fica como estava); senão a lista nova, o aviso e se o medidor precisa reabrir.
    `meter` é o medidor aberto (o endpoint dele diz se o "acompanha" ficou para trás, D-067)."""
    released = False

    def release() -> None:
        nonlocal released
        released = True
        close_meter()

    try:
        changed = platform.refresh_audio_devices(1 if meter_open else 0, release)
    except Exception as e:  # a lista antiga continua valendo
        log.debug("reler os dispositivos de áudio: %s", e)
        changed = False
    if not changed:
        if not released and follow and _behind_default(platform, combo, devices, meter):
            close_meter()                                  # D-067: o "acompanha" vai para o padrão de agora
            return MicRefresh(devices, "", True)
        return MicRefresh(devices, "", True) if released else None
    try:
        fresh = platform.list_input_devices()
        default = platform.default_input_device()
    except Exception as e:
        log.warning("listar microfones depois da mudança: %s", e)
        return MicRefresh(devices, "", released)
    keep = combo.currentData()
    kept = fill_mic_combo(combo, fresh, keep, default.id if default else None, follow=follow)
    old = {d.id for d in devices}
    notes = [f"novo: {', '.join(d.name for d in fresh if d.id not in old)}"] if any(d.id not in old for d in fresh) else []
    if keep and not kept:
        notes.append("o microfone escolhido saiu; usando o padrão")
    log.info("microfones relidos: %d na lista%s", len(fresh), " (o escolhido saiu)" if keep and not kept else "")
    return MicRefresh(fresh, "; ".join(notes), released or meter_open)


def _behind_default(platform: Platform, combo: QComboBox, devices: list[AudioDevice], meter: CaptureHandle | None) -> bool:
    """D-067: o medidor do "acompanha" aberto num microfone que não é o padrão de agora, que já está na lista."""
    if meter is None or combo.currentData() != FOLLOW_ID:
        return False
    try:
        now = platform.default_input_id()
    except Exception as e:  # sem a leitura do sistema: o medidor fica
        log.debug("microfone padrão de agora: %s", e)
        return False
    return bool(now) and now != meter.device_id and any(d.id == now for d in devices)


def fill_mic_combo(combo: QComboBox, devices: list[AudioDevice], keep_id: object, default_id: str | None,
                   follow: bool = False) -> bool:
    """Refaz o combo sem disparar a troca de microfone; True quando a escolha anterior segue na lista. Com `follow`, o
    primeiro item é o "acompanha", que sempre segue, e é ele que fica quando o escolhido saiu."""
    combo.blockSignals(True)
    try:
        combo.clear()
        if follow:
            combo.addItem(FOLLOW_TEXT, FOLLOW_ID)
        for d in devices:
            combo.addItem(d.name, d.id)
        i = 0 if follow and keep_id == FOLLOW_ID else (combo.findData(keep_id) if keep_id else -1)
        kept = i >= 0
        if not kept:
            i = 0 if follow else (combo.findData(default_id) if default_id else -1)
        combo.setCurrentIndex(max(0, i) if combo.count() else -1)
    finally:
        combo.blockSignals(False)
    combo.setToolTip(combo.currentText())
    return kept
