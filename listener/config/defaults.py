"""Tabela única das configurações (CP8; F1.2 M-24, absorve M-08): chave, padrão, escopo, rótulo, parser e faixa válida.
`scope` NOW aplica na hora; NEXT_RECORDING só na próxima gravação. Uma validação serve ao diálogo e ao gravador.
Atalhos: ctrl+alt+shift+letra (F1.1 A1): não colidem com Teams, Zoom, Meet, Chrome nem Edge; ctrl+alt sozinho é AltGr."""
from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass

from ..model import SettingScope
from .language import DEFAULT as DEFAULT_LANGUAGE, LANGUAGES

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
WHISPER_MODELS = ("large-v3-turbo", "small", "medium")       # F2 Q4: turbo padrão
TRANSCRIBERS = ("whisper", "llm")                            # F3 item 9: "llm" habilitado quando o modelo rápido tem áudio
HF_PROVIDER_ID = "huggingface"
LIVE_MODELS = ("small", "base")                              # F4 Q4: `small` padrão, `base` na degradação
LIVE_MIN_CORES = 8                                           # F4 Q1: ao vivo ligado por padrão só a partir daqui


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
    internal: bool = False               # escrita pelo app, não pelo usuário: fora do diálogo de configurações

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


def _bool(s: str) -> bool:
    low = s.strip().lower()
    if low in ("1", "true", "sim", "yes", "on"):
        return True
    if low in ("0", "false", "não", "nao", "no", "off", ""):
        return False
    raise ValueError(f"esperava sim ou não: {s!r}")


def _default_threads() -> int:
    return max(1, (os.cpu_count() or 2) - 2)


def _default_live() -> bool:
    """F4 Q1: legenda ao vivo ligada por padrão com 8 núcleos lógicos ou mais; senão desligada."""
    return (os.cpu_count() or 0) >= LIVE_MIN_CORES


_NOW, _NEXT = SettingScope.NOW, SettingScope.NEXT_RECORDING
_RATES = frozenset({8000, 16000, 22050, 24000, 32000, 44100, 48000})
_MIC_RATES = frozenset({8000, 16000, 32000, 44100, 48000})

SPECS: dict[str, SettingSpec] = {s.key: s for s in (
    # F5.3 (U-044, A-03): o idioma padrão das reuniões novas e do chat sobre todas; no grupo "Idioma e avisos", montado à
    # mão (o combo grava o código). Substitui o `llm.output_language` da F3, que só mudava a saída da LLM
    SettingSpec("app.language", DEFAULT_LANGUAGE, _NOW, "Idioma padrão", _text, choices=frozenset(LANGUAGES), phase="F5.3"),
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
    SettingSpec("hotkeys.history", "ctrl+alt+shift+h", _NOW, "Atalho: abrir o Histórico (cursor na busca)", _text, phase="F5"),  # U-028
    SettingSpec("log.path", "", _NOW, "Arquivo de log", _text),                 # vazio = <pasta do banco>/logs/listener.log
    SettingSpec("log.level", "INFO", _NOW, "Nível de log", _upper, choices=frozenset(LOG_LEVELS)),
    SettingSpec("ui.widget_pos", "", _NOW, "Posição do widget", _text),
    SettingSpec("ui.widget_size", "320x96", _NOW, "Tamanho do widget", _text),
    SettingSpec("ui.live_geometry", "", _NOW, "Posição e tamanho da janela Ao vivo", _text, phase="F4"),     # "x,y,largura,altura"
    SettingSpec("ui.live_on_top", True, _NOW, "Janela Ao vivo sempre visível", _bool, phase="F4"),
    SettingSpec("ui.history_geometry", "", _NOW, "Posição e tamanho da janela Histórico", _text, phase="F5"),     # "x,y,largura,altura"
    SettingSpec("ui.meeting_geometry", "", _NOW, "Posição e tamanho da janela da reunião", _text, phase="F5"),
    SettingSpec("ui.history_filters", "{}", _NOW, "Filtros do Histórico (pessoa e período)", _text, phase="F5", internal=True),
    SettingSpec("warmup.last_preset_id", "", _NOW, "Último preset", _text),
    SettingSpec("warmup.last_mic_device_id", "", _NOW, "Último microfone (id)", _text),
    SettingSpec("warmup.last_mic_device_name", "", _NOW, "Último microfone (nome)", _text),
    SettingSpec("warmup.last_source_kind", "system", _NOW, "Última fonte (tipo)", _text),
    SettingSpec("warmup.last_source_exe", "", _NOW, "Última fonte (executável)", _text),
    SettingSpec("warmup.last_in_room", "0", _NOW, "Última sala presencial", _text),
    # F2 (item 16): o worker lê o snapshot no claim ("vale para o próximo job"); o app lê a pausa e a estimativa
    SettingSpec("pipeline.transcriber", "whisper", _NOW, "Transcritor", _text, choices=frozenset(TRANSCRIBERS), phase="F2"),
    SettingSpec("pipeline.whisper_model", "large-v3-turbo", _NOW, "Modelo do Whisper", _text, choices=frozenset(WHISPER_MODELS),
                phase="F2"),
    SettingSpec("pipeline.whisper_hq", False, _NOW, "Alta precisão (wav2vec2, mais lento)", _bool, phase="F2"),
    SettingSpec("pipeline.glossary", "", _NOW, "Glossário (termos e nomes, separados por vírgula)", _text, phase="F2"),
    SettingSpec("pipeline.threshold", 0.60, _NOW, "Limiar de identificação (0,45 a 0,75)", float, low=0.45, high=0.75, phase="F2"),
    SettingSpec("pipeline.margin", 0.05, _NOW, "Margem entre os dois melhores perfis (0,01 a 0,20)", float, low=0.01, high=0.20,
                phase="F2"),
    SettingSpec("pipeline.models_dir", "", _NOW, "Pasta dos modelos (vazio = pasta padrão do sistema)", _text, phase="F2"),
    SettingSpec("pipeline.torch_threads", _default_threads(), _NOW, "Threads de CPU do processamento", int, low=1, high=64, phase="F2"),
    SettingSpec("pipeline.auto_start", True, _NOW, "Processar sozinho ao parar a gravação", _bool, phase="F2"),
    SettingSpec("pipeline.pause_on_record", True, _NOW, "Pausar a fila enquanto grava", _bool, phase="F2"),
    SettingSpec("pipeline.diar_window_s", 1200, _NOW, "Janela da separação de vozes (s)", int, low=300, high=7200, phase="F2"),
    SettingSpec("pipeline.whisper_window_s", 600, _NOW, "Janela da transcrição (s)", int, low=60, high=1800, phase="F2"),
    SettingSpec("pipeline.paused_until", "", _NOW, "Fila pausada até (lease da gravação)", _text, phase="F2", internal=True),
    SettingSpec("pipeline.paused_manual", False, _NOW, "Fila pausada pelo usuário", _bool, phase="F2", internal=True),
    SettingSpec("pipeline.rtf_json", "{}", _NOW, "Velocidade medida por etapa (estimativa)", _text, phase="F2", internal=True),
    # F3 (item 12): camada de LLM; o job `llm` grava o snapshot em `config_json` (nunca a chave)
    SettingSpec("llm.auto_tasks", True, _NOW, "Gerar título, pré-resumo, identidade e ata ao processar", _bool, phase="F3"),
    SettingSpec("llm.cost_alert_usd", 2.0, _NOW, "Alerta de custo por reunião (US$)", float, low=0.0, high=1000.0, phase="F3"),
    SettingSpec("llm.max_parallel", 2, _NOW, "Chamadas simultâneas à LLM", int, low=1, high=8, phase="F3"),
    SettingSpec("llm.audio_second_opinion", False, _NOW, "Segunda opinião por áudio na identidade (envia voz)", _bool, phase="F3"),
    SettingSpec("llm.glossary_enabled", True, _NOW, "Glossário pela LLM no warm-up", _bool, phase="F3"),
    SettingSpec("llm.store_payloads", True, _NOW, "Guardar pedido e resposta de cada chamada (comprimidos)", _bool, phase="F3"),
    SettingSpec("llm.proxy_url", "", _NOW, "Proxy HTTP(S) (vazio = o do sistema)", _text, phase="F3"),
    SettingSpec("llm.ca_bundle", "", _NOW, "Certificado raiz PEM (vazio = padrão)", _text, phase="F3"),
    SettingSpec("llm.system_certs", True, _NOW, "Aceitar certificados do sistema (truststore)", _bool, phase="F3"),
    # F4 (item 10): grupo "Ao vivo"; vale na próxima gravação, salvo o intervalo do resumo parcial, que vale em uso (CP8)
    SettingSpec("live.enabled", _default_live(), _NEXT, "Legenda ao vivo ligada (padrão do warm-up)", _bool, phase="F4"),
    SettingSpec("live.names", True, _NEXT, "Nomes ao vivo (padrão do warm-up)", _bool, phase="F4"),
    SettingSpec("live.model", "small", _NEXT, "Modelo da legenda ao vivo", _text, choices=frozenset(LIVE_MODELS), phase="F4"),
    SettingSpec("live.threads", 2, _NEXT, "Threads de CPU da legenda ao vivo", int, low=1, high=8, phase="F4"),
    SettingSpec("live.summary_interval_min", 5, _NOW, "Intervalo do resumo parcial (min; 0 desliga)", int, low=0, high=60,
                phase="F4"),                                   # 0: sem o custo do parcial, a ata segue (desvio do orquestrador, L5)
    SettingSpec("live.in_widget", True, _NEXT, "Mostrar a última legenda no widget", _bool, phase="F4"),
    # F5 (Dados): grupo "Busca" (a tela é do L5). `rag.auto_index` = vetores automáticos (o custo dos embeddings, Q1):
    # desligado, os jobs `index` continuam e gravam trechos e FTS (locais e grátis), só os vetores ficam de fora, salvo no
    # "reindexar" (pedido forçado). F5.1 (A-05): o embedder local não tem custo, logo o rótulo só fala de custo nos pagos;
    # sem `rag.local_model` (um modelo local só)
    SettingSpec("rag.auto_index", True, _NOW, "Vetores automáticos da busca por sentido (custo só nos pagos)", _bool, phase="F5"),
    SettingSpec("rag.top_k", 12, _NOW, "Trechos por pergunta no chat sobre todas as reuniões (4 a 30)", int, low=4, high=30,
                phase="F5"),
    SettingSpec("rag.max_per_meeting", 3, _NOW, "Trechos por reunião no chat sobre todas as reuniões (1 a 10)", int, low=1, high=10,
                phase="F5"),
)}

CAPTURE_KEYS = tuple(k for k in SPECS if k.startswith("capture."))
HOTKEY_KEYS = tuple(k for k in SPECS if k.startswith("hotkeys."))
PIPELINE_KEYS = tuple(k for k, s in SPECS.items() if k.startswith("pipeline.") and not s.internal)
LLM_KEYS = tuple(k for k, s in SPECS.items() if k.startswith("llm.") and not s.internal)
LIVE_KEYS = tuple(k for k, s in SPECS.items() if k.startswith("live.") and not s.internal)
RAG_KEYS = tuple(k for k, s in SPECS.items() if k.startswith("rag.") and not s.internal)

# Vistas derivadas para quem só precisa do texto padrão, do escopo ou do rótulo
DEFAULTS: dict[str, tuple[str, str]] = {k: (s.default_text, str(s.scope)) for k, s in SPECS.items()}
LABELS: dict[str, str] = {k: s.label for k, s in SPECS.items()}


def validate_setting(key: str, raw: str) -> str | None:
    """Erro (texto) ou None. Chaves desconhecidas são aceitas sem validação."""
    spec = SPECS.get(key)
    return spec.error(raw) if spec is not None else None
