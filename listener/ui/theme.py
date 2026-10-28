"""Sistema de design da F5.4 (L1): aplica os tokens (`ui/tokens.py`, reexportados daqui) no app e nos widgets.

- `apply(app, platform, scheme=None)` (A-03, A-09): a fonte (Segoe UI Variable, com queda para "Segoe UI", em px vezes
  `Platform.text_scale()`; o peso 600 pelas famílias "… Semibold", item 5), a paleta nos papéis que o `windows11` segue
  (A-01) e o `ThemeStyle` (`ui/style.py`) sobre `Platform.ui_style()`. `scheme` explícito vale para a sonda e os testes (o
  offscreen não tem esquema); sem ele, o do sistema, e `Unknown` é claro. Reaplica no `colorSchemeChanged` e na troca do
  contraste alto (A-07: `hints` guardado antes de encadear); com o contraste alto fica a paleta do sistema e os tokens
  saem dela (`system_tokens`, revisão da F5.4, R-03): cartão, faixa, selo, fala, linha selecionada e tons seguem o sistema.
- Widgets (A-02, A-10, passo 5 do A-21): `tone(w, Tone)` põe a cor do texto e `role(w, papel)` a fonte de um papel
  (`caption`, `body`, `body_strong`, `subtitle`, `clock`); `note(w)` é a nota (legenda no secundário); `set_kind` e `primary`
  marcam o primário (o padrão do Enter; o Enter num botão com foco aciona esse botão, R-02) e o destrutivo, `set_invalid`
  o campo com erro, `surface` uma janela inteira noutro esquema (no L1b, o widget de gravação, o marcador e os avisos;
  desde o L2 eles seguem o tema); `qcolor` é o único caminho (com os ícones) para um `QColor` (A-12). Itens de lista e
  tabela guardam o tom em `TONE_ROLE` e o `ThemeStyle` lê na pintura.
- Troca de tema (A-10): `app.setPalette` não alcança o que o tema pôs em cada widget; a varredura de `allWidgets()`, só na
  troca, repinta os tons (`theme_tone`), os ícones (`theme_icon`) e chama `retheme()` de quem pinta ou desenha HTML; depois
  `notifier().changed` avisa os tokens novos (ligado a métodos, nunca a lambda que capture widget).
- `release(app)` (A-09, R-06): o estilo em Python e os caches de ícones saem quando o laço de eventos volta, depois do
  `aboutToQuit` (o `run` do app, em toda saída), e no fim da sessão de testes: vivos até a derrubada do PySide, seriam
  destruídos fora de ordem na saída (0xC0000409).

O tema é estado do app, como a paleta que ele espelha: um só por processo, na thread da interface."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QAbstractButton, QApplication, QPushButton, QWidget

from ..log import get_logger
from .tokens import (BLACK, CONTRAST_PAIRS, CONTRAST_SCHEME, DARK, FONT_ROLES, FONTS, GRAPHIC_MIN, LIGHT, METRICS, SPEAKER_SURFACES,
                     STATUS_TONES, TEXT_MIN, WHITE, Fonts, FontToken, Metrics, Tokens, Tone, contrast, contrast_tokens, html_css,
                     luminance, mix, text_on)

if TYPE_CHECKING:
    from PySide6.QtGui import QAccessibilityHints, QStyleHints

    from ..platform.base import Platform

log = get_logger("ui.theme")

FALLBACK_FAMILY = "Segoe UI"                    # sem a Segoe UI Variable (Windows 10, offscreen dos testes)
TONE_PROP, FONT_PROP, SURFACE_PROP = "theme_tone", "theme_font", "theme_surface"
KIND_PROP, INVALID_PROP = "kind", "invalid"     # lidas pelo `ThemeStyle` na pintura (A-02, A-06)
PRIMARY, DANGER = "primary", "danger"           # os valores de `kind`
TONE_ROLE = Qt.ItemDataRole.UserRole + 0x7454   # o tom (`Tone.value`) do texto de um item de lista ou tabela (A-05)
SCHEMES = {LIGHT.scheme: LIGHT, DARK.scheme: DARK}


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


def font_for(role_name: str) -> QFont:
    """A fonte de um papel (`FONT_ROLES`) no fator de texto vigente."""
    if role_name not in FONT_ROLES:
        raise ValueError(f"papel de fonte desconhecido: {role_name}")
    return make_font(getattr(FONTS, role_name), _STATE.scale)


# ---------------------------------------------------------------- paleta
def tokens_for(scheme: Qt.ColorScheme | None) -> Tokens:
    """O escuro só com `Dark`; `Unknown` (offscreen, sistema sem preferência) é claro (A-03)."""
    return DARK if scheme == Qt.ColorScheme.Dark else LIGHT


def qcolor(value: str, alpha: float = 1.0) -> QColor:
    """Uma cor dos tokens ("#rrggbb") como `QColor`, com a opacidade de 0 a 1 (A-12: fora daqui e dos ícones, nada monta cor)."""
    color = QColor(value)
    if alpha < 1.0:
        color.setAlphaF(max(0.0, alpha))
    return color


def build_palette(t: Tokens) -> QPalette:
    """A paleta só nos papéis que o `windows11` segue (A-01: janela, base de listas e tabelas, texto, `Button` no cabeçalho
    de tabela e `Accent`), mais o texto de dica, a seleção de texto e o link; o resto fica com o sistema (papel não
    definido segue a paleta do Windows, também na troca de esquema). Desabilitado: texto em `disabled`."""
    roles = QPalette.ColorRole
    pal = QPalette()
    for role_id, value in ((roles.Window, t.window), (roles.WindowText, t.text), (roles.Base, t.input), (roles.Text, t.text),
                           (roles.Button, t.card), (roles.ButtonText, t.text), (roles.PlaceholderText, t.placeholder),
                           (roles.Accent, t.accent), (roles.Highlight, t.accent), (roles.HighlightedText, t.on_accent),
                           (roles.Link, t.accent), (roles.LinkVisited, t.accent), (roles.AlternateBase, t.card)):
        pal.setColor(role_id, QColor(value))
    for role_id in (roles.WindowText, roles.Text, roles.ButtonText, roles.PlaceholderText):
        pal.setColor(QPalette.ColorGroup.Disabled, role_id, QColor(t.disabled))
    return pal


def system_tokens(pal: QPalette) -> Tokens:
    """Contraste alto (R-03): os tokens das cores da paleta do sistema (janela, texto, base, destaque e o texto apagado)."""
    roles, active = QPalette.ColorRole, QPalette.ColorGroup.Active

    def color(role_id: QPalette.ColorRole, group: QPalette.ColorGroup = active) -> str:
        return pal.color(group, role_id).name()

    return contrast_tokens(window=color(roles.Window), text=color(roles.WindowText), base=color(roles.Base),
                           highlight=color(roles.Highlight), highlighted_text=color(roles.HighlightedText),
                           disabled=color(roles.WindowText, QPalette.ColorGroup.Disabled))


def _system_palette(app: QApplication) -> QPalette:
    """Contraste alto: a paleta do sistema no app (sem papel nosso, segue as trocas do Windows), lida de volta."""
    app.setPalette(QPalette())
    return app.palette()


# ---------------------------------------------------------------- widgets (A-02, A-10)
def surface_tokens(w: QWidget | None) -> Tokens:
    """Os tokens da janela de `w`: os do esquema marcado por `surface` ou os do tema vigente."""
    win = w.window() if w is not None else None
    return SCHEMES.get(str(win.property(SURFACE_PROP) or ""), _STATE.tokens) if win is not None else _STATE.tokens


def surface(w: QWidget, t: Tokens) -> None:
    """A janela `w` inteira nos tokens `t`, fora do tema vigente: a paleta própria e a marca `theme_surface`, que o
    `ThemeStyle`, `tone` e os ícones leem (F5.4 L1b: o widget de gravação, o marcador e os avisos, que seguem o tema desde o
    L2)."""
    w.setProperty(SURFACE_PROP, t.scheme)
    w.setPalette(build_palette(t))


def tone(w: QWidget, value: Tone | None) -> None:
    """A cor do texto de `w` (`WindowText` e `Text`, ativos e inativos; o desabilitado segue em `disabled`) no tom dos
    tokens da janela dele; None volta à herdada. O tom fica na propriedade `theme_tone` (a troca de tema repinta; num
    botão, o `ThemeStyle` o põe no texto, que o `windows11` só lê da própria opção)."""
    w.setProperty(TONE_PROP, value.value if value is not None else None)
    _paint_tone(w)


def _paint_tone(w: QWidget) -> None:
    name = w.property(TONE_PROP)
    if not name:
        w.setPalette(QPalette())                             # sem papel próprio: tudo herdado da janela
        return
    color = qcolor(surface_tokens(w).tone(Tone(str(name))))
    pal = w.palette()
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
        for role_id in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text):
            pal.setColor(group, role_id, color)
    w.setPalette(pal)


def retone(root: QWidget) -> None:
    """Os tons de `root` e dos filhos de novo, nos tokens da janela de cada um (quem foi montado antes de entrar nela)."""
    for w in (root, *root.findChildren(QWidget)):
        if w.property(TONE_PROP):
            _paint_tone(w)


def role(w: QWidget, role_name: str) -> None:
    """A fonte de um papel (`caption` 12 px, `body` 14, `body_strong` 14 semibold, `subtitle` 20, `clock` 24; U-052)."""
    w.setProperty(FONT_PROP, role_name)
    w.setFont(font_for(role_name))


def note(w: QWidget, value: Tone = Tone.SECONDARY) -> None:
    """A nota: legenda de 12 px (U-052) no tom dado, o secundário por padrão (4,5:1 nos dois temas, U-054)."""
    role(w, "caption")
    tone(w, value)


def set_kind(button: QAbstractButton, kind: str | None) -> None:
    """A-02: o primário (destaque preenchido, U-056) ou o destrutivo (vermelho) pela propriedade `kind`, que o `ThemeStyle`
    lê ao pintar; o primário é o botão padrão da janela (Enter)."""
    button.setProperty(KIND_PROP, kind)
    if isinstance(button, QPushButton):
        button.setDefault(kind == PRIMARY)
    button.update()


def primary(_window: QWidget, button: QPushButton) -> None:
    """O primário da janela (um por janela, U-056): `kind` e `setDefault(True)` nele, sem mexer nos outros botões. Revisão
    da F5.4 (R-02, desvio do A-02, que tirava o `autoDefault` dos outros): como no Windows, o Enter num botão com foco
    aciona esse botão (Cancelar cancela) e o Enter num campo aciona o primário."""
    set_kind(button, PRIMARY)


def set_invalid(w: QWidget, invalid: bool) -> None:
    """A borda `danger` no campo com erro (A-06), pintada pelo `ThemeStyle`."""
    w.setProperty(INVALID_PROP, True if invalid else None)
    w.update()


# ---------------------------------------------------------------- estado e troca de tema
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


def reduced_motion() -> bool:
    """Sem animação no sistema (Comportamento; revisão da F5.4, R-07): a preferência mora em `QAccessibilityHints` (A-07,
    guardados no `apply`); lida na hora, segue a troca sem sinal (quem anima consulta ao ligar o timer)."""
    access = _STATE.access
    return access is not None and access.motionPreference() == Qt.MotionPreference.ReducedMotion


def _sweep() -> None:
    """A-10: o que o tema pôs em cada widget, de novo nos tokens vigentes: o tom, o ícone e o `retheme()` de quem pinta à
    mão ou desenha HTML. Só na troca de tema (percorre todos os widgets do app)."""
    from . import icons

    for w in QApplication.allWidgets():
        if w.property(TONE_PROP):
            _paint_tone(w)
        if w.property(icons.ICON_PROP):
            icons.reapply(w)
        retheme = getattr(w, "retheme", None)
        if callable(retheme):
            retheme()


def _apply_scheme(app: QApplication) -> Tokens:
    from .style import ThemeStyle

    hints = _STATE.hints
    scheme = _STATE.forced if _STATE.forced is not None else (hints.colorScheme() if hints is not None else None)
    t, high = tokens_for(scheme), _high_contrast()
    if high:                                                    # contraste alto: a paleta do sistema e os tokens dela
        t = system_tokens(_system_palette(app))
    else:
        app.setPalette(build_palette(t))
    style = app.style()
    if isinstance(style, ThemeStyle):
        style.tokens = t
    changed = (t, high) != (_STATE.tokens, _STATE.high_contrast)
    _STATE.tokens, _STATE.high_contrast = t, high
    log.info("tema: %s%s", t.scheme, " com contraste alto (a paleta do sistema)" if high else "")
    if changed:
        _sweep()
        notifier().changed.emit(t)
    return t


def _reapply() -> None:
    """A troca de esquema ou de contraste com o app aberto: a paleta e os tokens de novo (a fonte vale na próxima abertura)."""
    app = QApplication.instance()
    if isinstance(app, QApplication) and _STATE.hints is not None:
        _apply_scheme(app)


def _install_style(app: QApplication, base: str) -> None:
    from .style import ThemeStyle

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
    """A-09 (R-06): o estilo base no lugar do `ThemeStyle` (o Qt apaga o antigo, dono dele), os caches de ícones vazios e a
    troca de esquema desligada. Pode ser chamada de novo; o `run` do app chama quando o laço volta (depois do `aboutToQuit`
    e da parada de emergência)."""
    from . import icons
    from .style import ThemeStyle

    target = app if app is not None else QApplication.instance()
    if _STATE.connected and _STATE.hints is not None and _STATE.access is not None and _STATE.notifier is not None:
        _STATE.hints.colorSchemeChanged.disconnect(_STATE.notifier.on_scheme_changed)
        _STATE.access.contrastPreferenceChanged.disconnect(_STATE.notifier.on_contrast_changed)
    _STATE.connected = False
    _STATE.hints = _STATE.access = None
    icons.clear_cache()
    if isinstance(target, QApplication):
        style = target.style()
        if isinstance(style, ThemeStyle) and target.setStyle(style.base_name) is None:
            target.setStyle("Fusion")
    _STATE.notifier = None                                   # sem dono no C++: o Python o apaga aqui


__all__ = ["BLACK", "CONTRAST_PAIRS", "CONTRAST_SCHEME", "DANGER", "DARK", "FONTS", "FONT_ROLES", "GRAPHIC_MIN", "INVALID_PROP",
           "KIND_PROP", "LIGHT", "METRICS", "PRIMARY", "SPEAKER_SURFACES", "STATUS_TONES", "TEXT_MIN", "TONE_ROLE", "WHITE",
           "FontToken", "Fonts", "Metrics", "ThemeNotifier", "Tokens", "Tone", "apply", "build_palette", "contrast", "contrast_tokens",
           "font_for", "html_css", "luminance", "make_font", "mix", "note", "notifier", "primary", "qcolor", "reduced_motion",
           "release", "retone", "role", "set_invalid", "set_kind", "surface", "surface_tokens", "system_tokens", "text_on",
           "text_scale", "tokens", "tokens_for", "tone"]
