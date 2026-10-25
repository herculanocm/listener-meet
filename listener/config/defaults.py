"""Tabela única das configurações (CP8; F1.2 M-24, absorve M-08): chave, padrão, escopo, rótulo, parser e faixa válida.
`scope` NOW aplica na hora; NEXT_RECORDING só na próxima gravação. Uma validação serve ao diálogo e ao gravador.
Atalhos: ctrl+alt+shift+letra (F1.1 A1): não colidem com Teams, Zoom, Meet, Chrome nem Edge; ctrl+alt sozinho é AltGr."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..model import SettingScope

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


@dataclass(frozen=True, slots=True)
class SettingSpec[T]:
    key: str
    default: T
    scope: SettingScope
    label: str
    parse: Callable[[str], T]
    low: float | None = None            # faixa numérica inclusiva
    high: float | None = None
    choices: frozenset[T] | None = None  # valores aceitos (taxas de amostragem, níveis de log)
    phase: str = "F1"

    @property
    def default_text(self) -> str:
        return str(self.default)

    def value(self, raw: str) -> T:
        """Converte o texto salvo; `ValueError` quando não é do tipo ou está fora da faixa."""
        v = self.parse(raw.strip())
        if self.choices is not None and v not in self.choices:
            raise ValueError(f"valor fora das opções {sorted(self.choices)}: {raw!r}")   # type: ignore[type-var]
        if isinstance(v, int | float) and not isinstance(v, bool):
            if self.low is not None and v < self.low:
                raise ValueError(f"abaixo do mínimo {self.low:g}: {raw!r}")
            if self.high is not None and v > self.high:
                raise ValueError(f"acima do máximo {self.high:g}: {raw!r}")
        return v

    def error(self, raw: str) -> str | None:
        """Mensagem de erro em PT-BR ou None quando o texto é válido (usada pelo diálogo e pelo gravador)."""
        try:
            self.value(raw)
        except (ValueError, TypeError) as e:
            return f"{self.label}: {e}"
        return None


def _text(s: str) -> str:
    return s


def _upper(s: str) -> str:
    return s.upper()


_NOW, _NEXT = SettingScope.NOW, SettingScope.NEXT_RECORDING
_RATES = frozenset({8000, 16000, 22050, 24000, 32000, 44100, 48000})
_MIC_RATES = frozenset({8000, 16000, 32000, 44100, 48000})

SPECS: dict[str, SettingSpec] = {s.key: s for s in (
    SettingSpec("capture.chunk_seconds", 60.0, _NEXT, "Duração do pedaço de áudio (s)", float, low=5, high=600),
    SettingSpec("capture.target_rate", 16000, _NEXT, "Taxa de amostragem gravada (Hz)", int, choices=_RATES),
    SettingSpec("capture.mic_rate", 16000, _NEXT, "Taxa do microfone (Hz)", int, choices=_MIC_RATES),
    SettingSpec("capture.silence_rms", 0.004, _NOW, "Limiar de silêncio (RMS, 0 a 1)", float, low=0.0, high=0.5),
    SettingSpec("capture.l_alert_seconds", 15.0, _NOW, "Alerta: microfone mudo após (s)", float, low=1, high=600),
    SettingSpec("capture.r_alert_seconds", 20.0, _NOW, "Alerta: saída muda após (s)", float, low=1, high=600),
    SettingSpec("capture.echo_corr_min", 0.25, _NOW, "Eco: correlação mínima para avisar (0 a 1)", float, low=0.0, high=1.0),
    SettingSpec("hotkeys.start", "ctrl+alt+shift+r", _NOW, "Atalho: iniciar", _text),
    SettingSpec("hotkeys.pause", "ctrl+alt+shift+p", _NOW, "Atalho: pausar/retomar", _text),
    SettingSpec("hotkeys.stop", "ctrl+alt+shift+s", _NOW, "Atalho: parar", _text),
    SettingSpec("hotkeys.mark", "ctrl+alt+shift+m", _NOW, "Atalho: marcador", _text),
    SettingSpec("hotkeys.mute", "ctrl+alt+shift+u", _NOW, "Atalho: mudo", _text),
    SettingSpec("log.path", "", _NOW, "Arquivo de log", _text),                 # vazio = <pasta do banco>/logs/listener.log
    SettingSpec("log.level", "INFO", _NOW, "Nível de log", _upper, choices=frozenset(LOG_LEVELS)),
    SettingSpec("ui.widget_pos", "", _NOW, "Posição do widget", _text),
    SettingSpec("ui.widget_size", "320x96", _NOW, "Tamanho do widget", _text),
    SettingSpec("warmup.last_preset_id", "", _NOW, "Último preset", _text),
    SettingSpec("warmup.last_mic_device_id", "", _NOW, "Último microfone (id)", _text),
    SettingSpec("warmup.last_mic_device_name", "", _NOW, "Último microfone (nome)", _text),
    SettingSpec("warmup.last_source_kind", "system", _NOW, "Última fonte (tipo)", _text),
    SettingSpec("warmup.last_source_exe", "", _NOW, "Última fonte (executável)", _text),
    SettingSpec("warmup.last_in_room", "0", _NOW, "Última sala presencial", _text),
)}

CAPTURE_KEYS = tuple(k for k in SPECS if k.startswith("capture."))
HOTKEY_KEYS = tuple(k for k in SPECS if k.startswith("hotkeys."))

# Vistas derivadas para quem só precisa do texto padrão, do escopo ou do rótulo
DEFAULTS: dict[str, tuple[str, str]] = {k: (s.default_text, str(s.scope)) for k, s in SPECS.items()}
LABELS: dict[str, str] = {k: s.label for k, s in SPECS.items()}


def validate_setting(key: str, raw: str) -> str | None:
    """Erro (texto) ou None. Chaves desconhecidas são aceitas sem validação."""
    spec = SPECS.get(key)
    return spec.error(raw) if spec is not None else None
