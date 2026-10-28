"""Os tokens da F5.4 (L1): cores do Windows 11 em claro e escuro, falantes, fonte e medidas, as contas de contraste do WCAG
2.1 e a folha de classes do HTML. Sem Qt: dados e contas puras. `ui/theme.py` os reexporta (é por lá que o app os usa) e
os aplica; este módulo existe para o `theme.py` ficar abaixo de 400 linhas e o `ThemeStyle` (`ui/style.py`) ler os tokens
sem importar o tema no nível do módulo.

- Tokens (spec, tabela "Tokens"): `LIGHT` e `DARK`, congelados; os dicionários de estado guardam um `Tone` e leem a cor do
  tema na hora (A-11).
- Contraste (A-13): `CONTRAST_PAIRS` é a matriz dos pares realmente usados; texto pede 4,5:1 e ícone, traço forte e falante
  3:1. Fora dela, de propósito: `disabled` (o WCAG isenta o apagado) e `placeholder`, `warning` e `stroke_strong` sobre
  `selected` (o placeholder só aparece no campo vazio, o estado sai em selo com fundo próprio e a caixa de seleção não fica
  sobre a linha selecionada).
- HTML (A-10, passo 6 do A-21): `html_css(t)` é a folha de classes (`note`, `success`, `warning`, `danger`, `info`,
  `accent`) que o `QTextBrowser` do tema põe no `document().setDefaultStyleSheet`; o HTML do app usa `class`, nunca cor."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

BLACK, WHITE = "#000000", "#ffffff"
TEXT_MIN, GRAPHIC_MIN = 4.5, 3.0                 # WCAG 2.1: texto comum; ícone, traço e o que não é texto


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
    check_h: int = 24              # caixa de seleção e rádio (Aceite 3)
    focus_w: int = 2               # o anel de foco (U-055)


FONTS, METRICS = Fonts(), Metrics()
FONT_ROLES = ("caption", "body", "body_strong", "subtitle", "clock")


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
    ("on_accent", ("accent", "accent_hover", "accent_pressed", "danger"), TEXT_MIN),   # o texto do primário e do destrutivo
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


# ---------------------------------------------------------------- HTML por classes (A-10)
HTML_CLASSES: dict[str, Tone] = {"note": Tone.SECONDARY, "success": Tone.SUCCESS, "warning": Tone.WARNING,
                                 "danger": Tone.DANGER, "info": Tone.INFO, "accent": Tone.ACCENT}


def html_css(t: Tokens) -> str:
    """A folha de classes do HTML nos tokens `t` (o `document().setDefaultStyleSheet` do `QTextBrowser` do tema): o HTML do
    app diz `class='note'`, nunca a cor, e a troca de tema só refaz a folha e o documento."""
    rules = [f".{name} {{ color: {t.tone(tone)}; }}" for name, tone in HTML_CLASSES.items()]
    return " ".join([*rules, f"a {{ color: {t.accent}; }}"])
