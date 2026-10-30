"""F5.9 (L1a): as decisões da vigia dos dispositivos e a linha de alerta do widget, sem Qt (de `recording_devices.py`).
A troca automática do microfone e da saída a partir da leitura do Core Audio (A-16, A-20), o escolhido que saiu e voltou
(R-04), a saída de comunicação como pista do "sem sinal" (A-20) e a linha de alerta por prioridade: a saúde dos canais, o
banco parado, o aviso dos dispositivos e o banco lento (A-21). F5.4 L2 (U-060): cada alerta tem um tipo (`kind`: `info`,
`success`, `warning` ou `error`, os da `InfoBar`), que o widget mostra no ícone e na cor da linha. Nomes de aparelho só
na tela (A-22)."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..capture.recorder import Recorder, SourceSpec
from ..model import DeviceReason, Health, LostReason, SourceKind
from ..platform.base import AudioDevice, Endpoint, Endpoints

ACTION_USE, ACTION_DEVICES, ACTION_OUTPUTS, ACTION_ROOM = "use", "devices", "outputs", "room"
DEVICES_BUTTON = "Dispositivos…"
ROOM_BUTTON = "Marcar como sala"                                                                                      # F5.9 (item 7)
ECHO_TEXT = "o microfone novo ouve a reunião: use fone ou marque a sala"
ECHO_TIP = "Marcar como sala liga 'Sala de reunião' nesta gravação: a identificação final trata o canal do microfone como a sala."
GONE_TEXT = {"L": "microfone escolhido saiu: gravando no padrão do Windows", "R": "saída escolhida saiu: gravando a saída padrão"}
BACK_TEXT = {"L": "microfone escolhido voltou", "R": "saída escolhida voltou"}
LOST_TEXT = {"L": "microfone desconectado; tentando reabrir…", "R": "saída desconectada; tentando reabrir…"}
NEVER_OPENED_TEXT = {"L": "microfone não iniciou; tentando de novo…", "R": "saída não iniciou; tentando de novo…"}   # F5.9 (item 2)
APP_LEFT_TEXT = "saída desconectada (o app saiu); tentando reabrir…"                                                 # F5.9 (item 1)
FALLBACK_TEXT = "app sem loopback: gravando o sistema inteiro"                                                       # F5.9 (item 4)


@dataclass(frozen=True, slots=True)
class Alert:
    """A linha de alerta do widget: o texto, o rótulo do botão (vazio = sem botão), a ação do clique, a dica e o tipo (o ícone
    e a cor da linha, F5.4 L2): erro no que parou de gravar, aviso no que pede atenção, informação no aparelho novo."""
    text: str
    button: str = ""
    action: str = ""
    tip: str = ""
    kind: str = "warning"


# ---------------------------------------------------------------- decisões (sem Qt)
def mic_action(reading: Endpoints, wanted: AudioDevice | None, open_id: str) -> tuple[str, bool] | None:
    """(motivo, strict) da troca automática do microfone, ou None. Acompanha: o padrão mudou. Escolhido: voltou (A-16,
    pergunta 10), sumiu ou, fora dele, o padrão mudou (o lugar dele é o padrão)."""
    if wanted is None:
        target = reading.capture_default
        return (DeviceReason.DEFAULT, False) if target and target != open_id else None
    if reading.mic(wanted.id) is not None:
        return (DeviceReason.BACK, True) if open_id != wanted.id else None
    if open_id == wanted.id or (reading.capture_default and open_id != reading.capture_default):
        return (DeviceReason.MISSING, False)
    return None


def output_action(reading: Endpoints, source: SourceSpec, open_id: str) -> str | None:
    """O motivo de reabrir o loopback do sistema inteiro, ou None; a fonte de um app segue o app em qualquer saída."""
    if source.kind != SourceKind.SYSTEM:
        return None
    if source.output_id and reading.output(source.output_id) is not None:
        return DeviceReason.BACK if open_id != source.output_id else None
    target = reading.render_default
    if not target or target == open_id:
        return None
    return DeviceReason.DEFAULT if source.follows else DeviceReason.MISSING


def missing_edge(episode: str, wanted: str, present: bool, opened: str) -> tuple[str, str]:
    """R-04 (item 18): (o episódio, o aviso). O escolhido `wanted` que sumiu do Core Audio enquanto outro aparelho grava abre
    o episódio e avisa "saiu" uma vez; quando ele grava de novo, "voltou"; trocar de escolhido (ou passar a acompanhar o
    padrão) encerra o episódio calado."""
    if episode and episode != wanted:
        episode = ""
    if wanted and not present and opened != wanted:
        return wanted, ("" if episode else "saiu")
    if episode and opened == wanted:
        return "", "voltou"
    return episode, ""


def comm_hint(reading: Endpoints | None, source: SourceSpec, open_id: str) -> Endpoint | None:
    """A-20: a saída de comunicação, quando difere da padrão e não é a gravada (a chamada pode estar nela)."""
    if reading is None or source.kind != SourceKind.SYSTEM:
        return None
    comm = reading.render_comm
    if not comm or comm == reading.render_default or comm == open_id:
        return None
    return reading.output(comm)


def lost_text(channel: str, reason: str) -> str:
    """F5.9: o alerta do canal perdido pelo motivo (`LostReason`): o que nunca abriu "não iniciou"; o app que saiu diz isso."""
    if reason == LostReason.NEVER_OPENED:
        return NEVER_OPENED_TEXT[channel]
    return APP_LEFT_TEXT if reason == LostReason.PROCESS_EXIT else LOST_TEXT[channel]


def alert_line(rec: Recorder, health: Mapping[str, str], notice: Alert | None, comm: Endpoint | None) -> Alert | None:
    """A linha de alerta por prioridade (A-21): a saúde dos canais, o banco parado, o aviso dos dispositivos, o banco
    lento."""
    snap = rec.snapshot
    for st in (rec.L, rec.R):
        if health.get(st.name) == Health.LOST:
            return Alert(lost_text(st.name, st.lost_reason), DEVICES_BUTTON, ACTION_DEVICES, kind="error")
    if health.get("R") == Health.ALERT:
        text = (f"saída sem sinal: a chamada pode estar em {comm.name} (comunicação)" if comm is not None
                else "saída sem sinal: a reunião está tocando em outro app?")
        return Alert(text, DEVICES_BUTTON, ACTION_DEVICES)
    if health.get("L") == Health.ALERT:
        return Alert(f"microfone sem sinal há mais de {int(rec.cfg.l_alert_seconds)}s", DEVICES_BUTTON, ACTION_DEVICES)
    db = health.get("db", Health.OK)
    if db == Health.ALERT:
        since = int(rec.elapsed() - (snap.pending_since or rec.elapsed()))
        return Alert(f"banco não responde há {since}s; {snap.pending} pedaço(s) retido(s) em memória", kind="error")
    if rec.source.fallback_exe:                           # F5.9 (item 4): abaixo da saúde, acima do aviso dos dispositivos
        return Alert(FALLBACK_TEXT, DEVICES_BUTTON, ACTION_DEVICES)
    if notice is not None:
        return notice
    if db == Health.WARN:
        return Alert(f"banco lento: {snap.pending} pedaço(s) na fila")
    return None
