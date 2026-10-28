"""Sistema de design da F5.4 (L1): os tokens do Windows 11 em claro e escuro (cores, falantes, fonte e medidas), as contas
de contraste do WCAG 2.1 (vindas de `ui/common.py`, que as reexporta) e `apply`, que põe fonte, paleta e estilo no app.

- Tokens (spec, tabela "Tokens"): `LIGHT` e `DARK`, congelados; `tokens()` é o do tema vigente (o claro antes do
  `apply`). Os dicionários de estado guardam um `Tone` e leem a cor do tema na hora (A-11).
- Contraste (A-13): `CONTRAST_PAIRS` é a matriz dos pares realmente usados; texto pede 4,5:1 e ícone, traço forte e
  falante 3:1. Fora dela, de propósito: `disabled` (o WCAG isenta o apagado) e `placeholder`, `warning` e `stroke_strong`
  sobre `selected` (o placeholder só aparece no campo vazio, o estado sai em selo com fundo próprio e a caixa de seleção
  não fica sobre a linha selecionada).
- `apply(app, platform, scheme=None)` (A-03, A-09): a fonte (Segoe UI Variable, com queda para "Segoe UI", em px vezes
  `Platform.text_scale()`; o peso 600 pelas famílias "… Semibold", item 5), a paleta nos papéis que o `windows11` segue
  (A-01) e o `ThemeStyle` sobre `Platform.ui_style()`. `scheme` explícito vale para a sonda e os testes (o offscreen não
  tem esquema); sem ele, o do sistema, e `Unknown` é claro. Reaplica no `colorSchemeChanged` e na troca do contraste alto
  (A-07: `hints` guardado antes de encadear); com o contraste alto fica a paleta do sistema. `notifier().changed` avisa os
  tokens novos (A-10: ligado a métodos, nunca a lambda que capture widget).
- `release(app)` (A-09, R-06): o estilo em Python sai quando o laço de eventos volta, depois do `aboutToQuit` (o `run` do
  app, em toda saída), e no fim da sessão de testes: vivo até a derrubada do PySide, seria destruído fora de ordem na saída
  (0xC0000409).

O tema é estado do app, como a paleta que ele espelha: um só por processo, na thread da interface."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication, QProxyStyle

from ..log import get_logger

if TYPE_CHECKING:
    from PySide6.QtGui import QAccessibilityHints, QStyleHints

    from ..platform.base import Platform

log = get_logger("ui.theme")

BLACK, WHITE = "#000000", "#ffffff"
TEXT_MIN, GRAPHIC_MIN = 4.5, 3.0                 # WCAG 2.1: texto comum; ícone, traço e o que não é texto
FALLBACK_FAMILY = "Segoe UI"                    # sem a Segoe UI Variable (Windows 10, offscreen dos testes)


class Tone(StrEnum):
    """O papel de uma cor de estado nos tokens: o que os dicionários de estado guardam (A-11), lido do tema na hora."""
    ACCENT = "accent"
    SUCCESS = "success"
    WARNING = "warning"
    DANGER = "danger"
    INFO = "info"
    NEUTRAL = "neutral"
    DISABLED = "disabled"
    TEXT = "text"
    SECONDARY = "secondary"


STATUS_TONES = (Tone.SUCCESS, Tone.WARNING, Tone.DANGER, Tone.INFO, Tone.NEUTRAL)   # os que têm fundo próprio (selo, faixa)


@dataclass(frozen=True, slots=True)
class FontToken:
    """Uma fonte do Fluent: a família da Segoe UI Variable (a "… Semibold" já é o peso 600) e o tamanho em px lógicos."""
    family: str
    px: int
    semibold: bool = False


@dataclass(frozen=True, slots=True)
class Fonts:
    caption: FontToken = FontToken("Segoe UI Variable Small", 12)
    body: FontToken = FontToken("Segoe UI Variable Text", 14)
    body_strong: FontToken = FontToken("Segoe UI Variable Text Semibold", 14, semibold=True)
    subtitle: FontToken = FontToken("Segoe UI Variable Display Semibold", 20, semibold=True)
    clock: FontToken = FontToken("Segoe UI Variable Display Semibold", 24, semibold=True)


@dataclass(frozen=True, slots=True)
class Metrics:
    """Medidas em px lógicos; sem sombra (preenchimento e borda)."""
    margin: int = 24               # da janela
    card_padding: int = 16         # dentro do cartão
    card_gap: int = 12             # entre cartões
    form_row: int = 8              # entre linhas de formulário
    radius: int = 4                # controle
    radius_card: int = 8           # cartão e janela flutuante
    control_h: int = 32            # controle e linha de tabela
    list_item_h: int = 36          # item de lista


FONTS, METRICS = Fonts(), Metrics()


@dataclass(frozen=True, slots=True)
class Tokens:
    """Os tokens de um esquema ("#rrggbb" em minúsculas, como `QColor.name()`); `*_bg` é o fundo do selo e da faixa."""
    scheme: str
    window: str
    card: str
    input: str
    hover: str
    selected: str
    stroke: str
    stroke_card: str
    stroke_strong: str
    text: str
    secondary: str
    placeholder: str
    disabled: str
    focus: str
    on_accent: str
    accent: str
    accent_hover: str
    accent_pressed: str
    success: str
    success_bg: str
    warning: str
    warning_bg: str
    danger: str
    danger_bg: str
    info: str
    info_bg: str
    neutral: str
    neutral_bg: str
    speakers: tuple[str, ...]
    fonts: Fonts = FONTS
    metrics: Metrics = METRICS

    def tone(self, tone: Tone) -> str:
        return str(getattr(self, tone.value))

    def background(self, tone: Tone) -> str:
        """O fundo de um tom de estado (selo, faixa); só os de `STATUS_TONES` têm."""
        if tone not in STATUS_TONES:
            raise ValueError(f"o tom {tone} não tem fundo próprio")
        return str(getattr(self, f"{tone.value}_bg"))

    def speaker(self, index: int) -> str:
        """A cor do falante `index` (a partir de 0), em volta nas 8."""
        return self.speakers[index % len(self.speakers)]


LIGHT = Tokens(
    "light", window="#f3f3f3", card="#fbfbfb", input="#ffffff", hover="#f0f0f0", selected="#e0ecf6", stroke="#e5e5e5",
    stroke_card="#e5e5e5", stroke_strong="#8a8a8a", text="#1a1a1a", secondary="#5d5d5d", placeholder="#6b6b6b",
    disabled="#9e9e9e", focus="#1a1a1a", on_accent="#ffffff", accent="#005fb8", accent_hover="#186ebe", accent_pressed="#004c94",
    success="#0f7b0f", success_bg="#dff6dd", warning="#9d5d00", warning_bg="#fff4ce", danger="#c42b1c", danger_bg="#fde7e9",
    info="#005fb8", info_bg="#ebf3fc", neutral="#5d5d5d", neutral_bg="#ebebeb",
    speakers=("#0f6cbd", "#038387", "#ca5010", "#c239b3", "#107c10", "#8764b8", "#c50f1f", "#486991"))
DARK = Tokens(
    "dark", window="#202020", card="#2b2b2b", input="#1c1c1c", hover="#2e2e2e", selected="#273840", stroke="#3a3a3a",
    stroke_card="#1c1c1c", stroke_strong="#9a9a9a", text="#ffffff", secondary="#c5c5c5", placeholder="#a0a0a0",
    disabled="#727272", focus="#ffffff", on_accent="#000000", accent="#60cdff", accent_hover="#5abce9", accent_pressed="#53aad2",
    success="#6ccb5f", success_bg="#393d1b", warning="#fce100", warning_bg="#433519", danger="#ff99a4", danger_bg="#442726",
    info="#60cdff", info_bg="#1f2b38", neutral="#c5c5c5", neutral_bg="#3a3a3a",
    speakers=("#479ef5", "#00b7c3", "#f7894a", "#da7ed0", "#54b054", "#b696e0", "#dc626d", "#88a4c8"))

# A-13: (frente, fundos, mínimo) dos pares usados; o teste dos tokens confere nos dois esquemas
_SURFACES = ("window", "card", "input", "hover", "selected")
CONTRAST_PAIRS: tuple[tuple[str, tuple[str, ...], float], ...] = (
    ("text", _SURFACES, TEXT_MIN),
    ("secondary", _SURFACES, TEXT_MIN),
    ("placeholder", ("window", "card", "input", "hover"), TEXT_MIN),
    ("accent", ("window", "card", "input", "hover", "selected"), TEXT_MIN),              # link e texto de destaque
    ("on_accent", ("accent", "accent_hover", "accent_pressed"), TEXT_MIN),             # o texto do primário
    *((tone.value, _SURFACES, TEXT_MIN) for tone in STATUS_TONES if tone != Tone.WARNING),
    ("warning", ("window", "card", "input", "hover"), TEXT_MIN),
    *((tone.value, (f"{tone.value}_bg",), TEXT_MIN) for tone in STATUS_TONES),          # selo e faixa
    *(("text", (f"{tone.value}_bg",), TEXT_MIN) for tone in STATUS_TONES),
    ("stroke_strong", ("window", "card", "input", "hover"), GRAPHIC_MIN),              # caixa de seleção, borda forte
    ("focus", ("window", "card", "input", "hover"), GRAPHIC_MIN),                      # o anel de foco
)
SPEAKER_SURFACES = _SURFACES                    # falantes: 3:1 sobre todas as superfícies (o ponto na linha selecionada)


# ---------------------------------------------------------------- contraste (WCAG 2.1)
def _rgb(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def luminance(color: str) -> float:
    """Luminância relativa do WCAG 2.1 de uma cor "#rrggbb": 0 no preto, 1 no branco."""
    def linear(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(c) for c in _rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    """Contraste do WCAG entre duas cores, de 1 a 21 (texto comum pede 4,5)."""
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def text_on(background: str) -> str:
    """Preto ou branco, o que contrastar mais com o fundo: nunca abaixo de 4,58:1 (o pior caso é um cinza médio)."""
    return BLACK if contrast(BLACK, background) >= contrast(WHITE, background) else WHITE


def mix(a: str, b: str, amount: float) -> str:
    """`a` com `amount` (0 a 1) de `b`, em "#rrggbb"."""
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(_rgb(a), _rgb(b), strict=True))


# ---------------------------------------------------------------- fonte
@lru_cache(maxsize=1)
def _families() -> frozenset[str]:
    """As famílias instaladas, lidas uma vez (item 5)."""
    return frozenset(QFontDatabase.families())


def make_font(token: FontToken, scale: float = 1.0) -> QFont:
    """A fonte de um token em px vezes o fator de texto do sistema; sem a Segoe UI Variable, a "Segoe UI" (no peso 600
    quando o token pede: a família variável dá o 600 pela "… Semibold", que o `DemiBold` sairia Bold 700)."""
    available = token.family in _families()
    font = QFont(token.family if available else FALLBACK_FAMILY)
    font.setPixelSize(max(1, round(token.px * scale)))
    if token.semibold and not available:
        font.setWeight(QFont.Weight.DemiBold)
    return font


# ---------------------------------------------------------------- paleta e estilo
def tokens_for(scheme: Qt.ColorScheme | None) -> Tokens:
    """O escuro só com `Dark`; `Unknown` (offscreen, sistema sem preferência) é claro (A-03)."""
    return DARK if scheme == Qt.ColorScheme.Dark else LIGHT


def build_palette(t: Tokens) -> QPalette:
    """A paleta só nos papéis que o `windows11` segue (A-01: janela, base de listas e tabelas, texto, `Button` no cabeçalho
    de tabela e `Accent`), mais o texto de dica, a seleção de texto e o link; o resto fica com o sistema (papel não
    definido segue a paleta do Windows, também na troca de esquema). Desabilitado: texto em `disabled`."""
    roles = QPalette.ColorRole
    pal = QPalette()
    for role, value in ((roles.Window, t.window), (roles.WindowText, t.text), (roles.Base, t.input), (roles.Text, t.text),
                        (roles.Button, t.card), (roles.ButtonText, t.text), (roles.PlaceholderText, t.placeholder),
                        (roles.Accent, t.accent), (roles.Highlight, t.accent), (roles.HighlightedText, t.on_accent),
                        (roles.Link, t.accent), (roles.LinkVisited, t.accent)):
        pal.setColor(role, QColor(value))
    for role in (roles.WindowText, roles.Text, roles.ButtonText, roles.PlaceholderText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(t.disabled))
    return pal


class ThemeStyle(QProxyStyle):
    """O estilo do app por cima do estilo base (`windows11` no Windows, Fusion no fake dos testes). F5.4 L1a: só a
    estrutura, com os tokens do tema vigente; a pintura (primário, destrutivo, cartão, abas, foco, seleção, borda de erro;
    A-02, A-05, A-06) vem no L1b, sobrescrevendo só os `draw*` e o `polish(widget)` (A-04: o proxy em Python custa ~+6 %
    na abertura só nos `draw*` e ~+37 % com `pixelMetric`/`styleHint`)."""

    def __init__(self, base: str, tokens: Tokens) -> None:
        super().__init__(base)
        self.base_name = base
        self.tokens = tokens


class ThemeNotifier(QObject):
    """O `theme.changed` da spec (A-10): os tokens novos depois de cada troca. Ligue a métodos, nunca a lambda que capture
    o widget (o ciclo pelo C++ prende o widget até a saída, R-06)."""
    changed = Signal(object)

    def on_scheme_changed(self, _scheme: Qt.ColorScheme) -> None:
        _reapply()

    def on_contrast_changed(self, _preference: Qt.ContrastPreference) -> None:
        _reapply()


@dataclass(slots=True)
class _State:
    tokens: Tokens = LIGHT
    high_contrast: bool = False
    forced: Qt.ColorScheme | None = None        # o esquema pedido no `apply` (sonda e testes); None segue o sistema
    scale: float = 1.0
    hints: QStyleHints | None = None            # A-07: guardados, nunca encadeados
    access: QAccessibilityHints | None = None
    notifier: ThemeNotifier | None = None
    connected: bool = False


_STATE = _State()


def tokens() -> Tokens:
    """Os tokens do tema vigente (o claro antes do `apply`)."""
    return _STATE.tokens


def text_scale() -> float:
    """O fator de texto do sistema lido no último `apply` (vale até a próxima abertura, A-08)."""
    return _STATE.scale


def notifier() -> ThemeNotifier:
    if _STATE.notifier is None:
        _STATE.notifier = ThemeNotifier()
    return _STATE.notifier


def _high_contrast() -> bool:
    access = _STATE.access
    return access is not None and access.contrastPreference() == Qt.ContrastPreference.HighContrast


def _apply_scheme(app: QApplication) -> Tokens:
    hints = _STATE.hints
    scheme = _STATE.forced if _STATE.forced is not None else (hints.colorScheme() if hints is not None else None)
    t, high = tokens_for(scheme), _high_contrast()
    app.setPalette(QPalette() if high else build_palette(t))    # contraste alto: a paleta do sistema, sem papel nosso
    style = app.style()
    if isinstance(style, ThemeStyle):
        style.tokens = t
    changed = (t, high) != (_STATE.tokens, _STATE.high_contrast)
    _STATE.tokens, _STATE.high_contrast = t, high
    log.info("tema: %s%s", t.scheme, " com contraste alto (a paleta do sistema)" if high else "")
    if changed:
        notifier().changed.emit(t)
    return t


def _reapply() -> None:
    """A troca de esquema ou de contraste com o app aberto: a paleta e os tokens de novo (a fonte vale na próxima abertura)."""
    app = QApplication.instance()
    if isinstance(app, QApplication) and _STATE.hints is not None:
        _apply_scheme(app)


def _install_style(app: QApplication, base: str) -> None:
    current = app.style()
    if isinstance(current, ThemeStyle) and current.base_name == base:
        return
    app.setStyle(ThemeStyle(base, _STATE.tokens))           # o app fica dono do estilo (sai no `release`)


def apply(app: QApplication, platform: Platform, scheme: Qt.ColorScheme | None = None) -> Tokens:
    """Fonte, estilo e paleta no app, antes da primeira janela (A-09); devolve os tokens aplicados."""
    hints = app.styleHints()                                 # A-07: guardado antes de encadear
    _STATE.hints, _STATE.access = hints, hints.accessibility()
    _STATE.forced = scheme
    _STATE.scale = platform.text_scale()
    _install_style(app, platform.ui_style())
    font = make_font(FONTS.body, _STATE.scale)
    app.setFont(font)
    if not _STATE.connected:
        n = notifier()
        hints.colorSchemeChanged.connect(n.on_scheme_changed)
        _STATE.access.contrastPreferenceChanged.connect(n.on_contrast_changed)
        _STATE.connected = True
    t = _apply_scheme(app)
    log.info("tema: fonte %s %d px (texto %.2f), estilo %s", font.family(), font.pixelSize(), _STATE.scale, platform.ui_style())
    return t


def release(app: QApplication | None = None) -> None:
    """A-09 (R-06): o estilo base no lugar do `ThemeStyle` (o Qt apaga o antigo, dono dele) e a troca de esquema desligada.
    Pode ser chamada de novo; o `run` do app chama quando o laço volta (depois do `aboutToQuit` e da parada de emergência)."""
    target = app if app is not None else QApplication.instance()
    if _STATE.connected and _STATE.hints is not None and _STATE.access is not None and _STATE.notifier is not None:
        _STATE.hints.colorSchemeChanged.disconnect(_STATE.notifier.on_scheme_changed)
        _STATE.access.contrastPreferenceChanged.disconnect(_STATE.notifier.on_contrast_changed)
    _STATE.connected = False
    _STATE.hints = _STATE.access = None
    if isinstance(target, QApplication):
        style = target.style()
        if isinstance(style, ThemeStyle) and target.setStyle(style.base_name) is None:
            target.setStyle("Fusion")
    _STATE.notifier = None                                   # sem dono no C++: o Python o apaga aqui
