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
        now = _default_now(platform, devices) if follow else None
        if now is not None:
            _say_now(combo, now)                           # F5.9 (item 8): o "agora X" acompanha o padrão sem reler a lista
        if not released and now is not None and _behind_default(combo, meter, now):
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
    kept = fill_mic_combo(combo, fresh, keep, default, follow=follow)
    old = {d.id for d in devices}
    notes = [f"novo: {', '.join(d.name for d in fresh if d.id not in old)}"] if any(d.id not in old for d in fresh) else []
    if keep and not kept:
        notes.append("o microfone escolhido saiu; usando o padrão")
    log.info("microfones relidos: %d na lista%s", len(fresh), " (o escolhido saiu)" if keep and not kept else "")
    return MicRefresh(fresh, "; ".join(notes), released or meter_open)


def _default_now(platform: Platform, devices: list[AudioDevice]) -> AudioDevice | None:
    """O microfone padrão de agora (D-067: o padrão de console), quando já está na lista; None sem a leitura do sistema."""
    try:
        now = platform.default_input_id()
    except Exception as e:  # sem a leitura do sistema: nada muda
        log.debug("microfone padrão de agora: %s", e)
        return None
    return next((d for d in devices if d.id == now), None) if now else None


def _behind_default(combo: QComboBox, meter: CaptureHandle | None, now: AudioDevice) -> bool:
    """D-067: o medidor do "acompanha" aberto num microfone que não é o padrão de agora."""
    return meter is not None and combo.currentData() == FOLLOW_ID and now.id != meter.device_id


def follow_text(default: AudioDevice | None) -> str:
    """F5.9 (item 8): "Padrão do Windows (acompanha): agora <aparelho>", com o padrão de console de agora (A-20, como o
    painel e a tabela de fontes); sem ele, só o "acompanha". O nome do aparelho só na tela (A-22)."""
    return f"{FOLLOW_TEXT}: agora {default.name}" if default is not None and default.name else FOLLOW_TEXT


def _say_now(combo: QComboBox, now: AudioDevice) -> None:
    """O item "acompanha" diz o padrão de agora; a dica acompanha quando ele é o escolhido."""
    if combo.count() and combo.itemData(0) == FOLLOW_ID and combo.itemText(0) != follow_text(now):
        combo.setItemText(0, follow_text(now))
        if combo.currentIndex() == 0:
            combo.setToolTip(combo.currentText())


def fill_mic_combo(combo: QComboBox, devices: list[AudioDevice], keep_id: object, default: AudioDevice | None,
                   follow: bool = False) -> bool:
    """Refaz o combo sem disparar a troca de microfone; True quando a escolha anterior segue na lista. Com `follow`, o
    primeiro item é o "acompanha" (com o padrão de agora, F5.9), que sempre segue, e é ele que fica quando o escolhido
    saiu; sem `follow`, o padrão é o item que fica."""
    combo.blockSignals(True)
    try:
        combo.clear()
        if follow:
            combo.addItem(follow_text(default), FOLLOW_ID)
        for d in devices:
            combo.addItem(d.name, d.id)
        i = 0 if follow and keep_id == FOLLOW_ID else (combo.findData(keep_id) if keep_id else -1)
        kept = i >= 0
        if not kept:
            i = 0 if follow else (combo.findData(default.id) if default is not None else -1)
        combo.setCurrentIndex(max(0, i) if combo.count() else -1)
    finally:
        combo.blockSignals(False)
    combo.setToolTip(combo.currentText())
    return kept
