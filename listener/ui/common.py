"""Utilidades da interface: níveis em dB, cores de saúde, formatação; F5: área de transferência com texto e HTML; a
tradução pt-BR dos textos padrão do Qt. F5.3 (U-047, A-08): as cores da barra da fila por estado e o contraste do texto
sobre elas (WCAG 2.1, 4,5:1), nos temas claro e escuro; (U-048) as do aviso que some sozinho, por tipo. F5.4: as contas
de contraste moram em `ui/theme.py` e seguem reexportadas daqui (A-13); os dicionários de estado guardam o tom (`Tone`) e a
cor sai dos tokens do tema vigente na hora (A-11). F5.4 L2: o widget de gravação, o marcador e os avisos seguem o tema (sai
o `DARK_SURFACE`) e `fit_to_screen` põe a janela inteira, com a moldura, dentro da área livre da tela (A-17)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

from ..model import AppState, Health, JobStatus
from .theme import BLACK, WHITE, Tokens, Tone, contrast, luminance, mix, text_on, tokens

if TYPE_CHECKING:
    from PySide6.QtCore import QCoreApplication, QTranslator
    from PySide6.QtWidgets import QWidget

QT_LOCALE = "pt_BR"             # a interface é em pt-BR (i18n antes da F6): os botões padrão do Qt seguem o mesmo idioma

HEALTH_TONES: dict[str, Tone] = {Health.OK: Tone.SUCCESS, Health.WARN: Tone.WARNING, Health.ALERT: Tone.DANGER,
                                 Health.MUTED: Tone.NEUTRAL, Health.PAUSED: Tone.WARNING, Health.OFF: Tone.DISABLED,
                                 Health.LOST: Tone.DANGER}
HEALTH_TEXT: dict[str, str] = {Health.OK: "ok", Health.WARN: "sem sinal", Health.ALERT: "MUDO", Health.MUTED: "mudo",
                               Health.PAUSED: "pausado", Health.OFF: "desligado", Health.LOST: "dispositivo perdido"}

# o estado do app (gravando, pausado, salvando): o ícone da bandeja e o estado escrito no widget
STATE_TONES: dict[str, Tone] = {AppState.IDLE: Tone.NEUTRAL, AppState.RECORDING: Tone.DANGER, AppState.PAUSED: Tone.WARNING,
                                AppState.SAVING: Tone.INFO}

# F5.3 (A-08): a barra da fila na cor do estado; F5.4: a cor do tom nos tokens de cada tema. O pendente fica no neutro e o
# cancelado no apagado, hachurado (as listras na cor contrária à do número): os dois cinzas não se confundem.
JOB_STATUS_TONES: dict[str, Tone] = {JobStatus.PENDING: Tone.NEUTRAL, JobStatus.RUNNING: Tone.INFO, JobStatus.PAUSED: Tone.WARNING,
                                     JobStatus.DONE: Tone.SUCCESS, JobStatus.ERROR: Tone.DANGER, JobStatus.CANCELLED: Tone.DISABLED}
JOB_STATUS_HATCHED: frozenset[str] = frozenset({JobStatus.CANCELLED})
JOB_STATUS_COLOR_NAMES: dict[str, str] = {JobStatus.PENDING: "barra cinza", JobStatus.RUNNING: "barra azul, com uma faixa que anda",
                                          JobStatus.PAUSED: "barra âmbar", JobStatus.DONE: "barra verde", JobStatus.ERROR: "barra vermelha",
                                          JobStatus.CANCELLED: "barra listrada"}
# F5.3 (U-048, A-14): o aviso que some sozinho, com a faixa na cor do tipo (as chaves são os valores de `ui.toast.NoticeKind`),
# os mesmos tons da barra da fila: verde pronto, azul informação, vermelho erro. F5.4 L2 (U-068): o aviso segue o tema, com a
# faixa e o ícone do tipo nos tokens vigentes (o texto em `text` e `secondary` sobre `card`, a faixa a 3:1; matriz do A-13).
NOTICE_TONES: dict[str, Tone] = {"pronto": Tone.SUCCESS, "informação": Tone.INFO, "erro": Tone.DANGER}
TRACK_MIX = 0.15                # o trilho: a base da tabela com 15 % da cor do texto (claro no tema claro, escuro no escuro)
BAND_OPACITY = 0.4              # a faixa do "rodando": clareia sob texto preto e escurece sob texto branco (o contraste só sobe)


@dataclass(frozen=True, slots=True)
class BarColors:
    """As cores da barra de um job (A-08): o número fica preto ou branco pela luminância do que está embaixo dele."""
    track: str                  # o trilho, da palette() da tabela
    fill: str                   # o preenchido, na cor do estado
    text: str                   # o número sobre o preenchido
    track_text: str             # o número sobre o trilho
    band: str                   # a faixa animada sobre o preenchido
    track_band: str             # a faixa animada sobre o trilho
    hatch: str | None = None    # as listras do cancelado


def _resolve(t: Tokens | None) -> Tokens:
    return t if t is not None else tokens()


def health_color(health: str, t: Tokens | None = None) -> str:
    """A cor da saúde de um canal nos tokens `t` (os do tema vigente quando não dados); a desconhecida sai apagada."""
    return _resolve(t).tone(HEALTH_TONES.get(health, Tone.DISABLED))


def state_color(state: str, t: Tokens | None = None) -> str:
    """A cor do estado do app (o desconhecido sai neutro)."""
    return _resolve(t).tone(STATE_TONES.get(state, Tone.NEUTRAL))


def job_status_color(status: str, t: Tokens | None = None) -> str:
    """A cor da barra de um job no estado (o desconhecido sai como pendente)."""
    return _resolve(t).tone(JOB_STATUS_TONES.get(status, JOB_STATUS_TONES[JobStatus.PENDING]))


def bar_colors(status: str, base: str, text: str, t: Tokens | None = None) -> BarColors:
    """As cores da barra de um job no estado `status` sobre a tabela de base `base` e texto `text` (cores da palette(),
    "#rrggbb"), nos tokens `t` (os do tema vigente quando não dados); um estado desconhecido sai como pendente."""
    return _bar_colors(status, base, text, _resolve(t))


@lru_cache(maxsize=64)
def _bar_colors(status: str, base: str, text: str, t: Tokens) -> BarColors:
    """Em cache (com os tokens na chave: a troca de tema não reaproveita): a faixa do "rodando" repinta a coluna a cada 120 ms."""
    fill = job_status_color(status, t)
    track = mix(base, text, TRACK_MIX)
    on_fill, on_track = text_on(fill), text_on(track)
    contrary = WHITE if on_fill == BLACK else BLACK
    return BarColors(track, fill, on_fill, on_track, contrary, WHITE if on_track == BLACK else BLACK,
                     contrary if status in JOB_STATUS_HATCHED else None)


def install_translations(app: QCoreApplication) -> QTranslator | None:
    """Botões e textos padrão do Qt em pt-BR (Sim e Não, Salvar, Cancelar, Fechar, Próximo e Concluir no assistente) pela
    tradução que vem com o PySide6 (`qtbase_pt_BR.qm`); sem ela ficavam em inglês (visto no Windows em 25/10: "Next",
    "Save", "Yes"). None quando o arquivo não existe: os botões seguem em inglês."""
    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator

    translator = QTranslator(app)
    if not translator.load(QLocale(QT_LOCALE), "qtbase", "_", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        return None
    app.installTranslator(translator)
    return translator


def level_fraction(rms_value: float, floor_db: float = -60.0) -> float:
    """RMS (0 a 1) -> fração 0 a 1 numa escala de dB com piso em -60 dBFS."""
    db = 20.0 * math.log10(max(float(rms_value), 1e-6))
    return max(0.0, min(1.0, (db - floor_db) / -floor_db))


def human_size(n: float) -> str:
    """Tamanho com vírgula decimal ("446,4 KB", U-032)."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}".replace(".", ",")
        n /= 1024
    return f"{n:.1f} TB".replace(".", ",")


def clamp_to_screens(x: int, y: int, w: int, h: int, screens: list[tuple[int, int, int, int]]) -> tuple[int, int] | None:
    """Mantém a janela (x, y, w, h) dentro do monitor com que mais se sobrepõe (retângulos x, y, largura, altura).
    None quando ela não toca monitor nenhum (monitor desplugado): quem chama volta à posição padrão (F1.1 A9)."""
    best, best_area = None, 0
    for sx, sy, sw, sh in screens:
        ix = max(0, min(x + w, sx + sw) - max(x, sx))
        iy = max(0, min(y + h, sy + sh) - max(y, sy))
        if ix * iy > best_area:
            best, best_area = (sx, sy, sw, sh), ix * iy
    if best is None:
        return None
    sx, sy, sw, sh = best
    return int(min(max(x, sx), sx + sw - w)), int(min(max(y, sy), sy + sh - h))


type Box = tuple[int, int, int, int]          # x, y, largura, altura


def fit_rect(frame: Box, client: tuple[int, int], area: Box, minimum: tuple[int, int] = (0, 0)) -> Box:
    """A-17: o retângulo da moldura (`frame`, com a barra de título) e o tamanho de dentro (`client`) de uma janela que cabe
    na área livre `area`: o de dentro encolhe o que passar (nunca abaixo de `minimum`, o mínimo do layout) e a moldura volta
    para dentro da área. Devolve a posição da moldura e o tamanho de dentro."""
    fx, fy, fw, fh = frame
    ax, ay, aw, ah = area
    extra_w, extra_h = fw - client[0], fh - client[1]
    width = max(minimum[0], min(client[0], aw - extra_w))
    height = max(minimum[1], min(client[1], ah - extra_h))
    x = max(ax, min(fx, ax + aw - (width + extra_w)))
    y = max(ay, min(fy, ay + ah - (height + extra_h)))
    return x, y, width, height


def fit_to_screen(w: QWidget) -> None:
    """A-17 (F5.4 L2): a janela inteira, com a moldura (`frameGeometry`), dentro da área livre da tela dela, no lugar da regra
    dos 90 % ([fato] no notebook do dono a 150 %, 1280 x 672 lógicos, sobravam 605 px de altura). Chamada no `showEvent` de
    quem a usa (a moldura só existe com a janela nativa) e reutilizável pelos outros lotes (Configurações e reunião abrem no
    limite)."""
    from PySide6.QtGui import QGuiApplication

    screen = w.screen() or QGuiApplication.primaryScreen()
    if screen is None:
        return
    g, f, a = w.geometry(), w.frameGeometry(), screen.availableGeometry()
    x, y, width, height = fit_rect((f.x(), f.y(), f.width(), f.height()), (g.width(), g.height()),
                                   (a.x(), a.y(), a.width(), a.height()), (w.minimumWidth(), w.minimumHeight()))
    if (width, height) != (g.width(), g.height()):
        w.resize(width, height)
    if (x, y) != (f.x(), f.y()):
        w.move(x, y)                                  # numa janela, `move` posiciona a moldura


def copy_rich(plain: str, rich_html: str | None = None) -> None:
    """Põe na área de transferência o texto e, quando dado, o mesmo em HTML (`QMimeData`): o Outlook e o Teams colam com
    formatação, o bloco de notas cola o texto (U-011, U-029).

    O `QMimeData` nasce no C++ (`QStandardItemModel.mimeData`, esvaziado em seguida) e não pelo construtor do Python: um
    `QMimeData()` do Python é a subclasse-ponte do PySide, que a área de transferência destrói na saída do app, depois da
    finalização do interpretador, e o destrutor dela chama o Python já finalizado (falha de segmentação ao sair, vista no
    offscreen com o app saindo pelo `aboutToQuit`)."""
    from PySide6.QtGui import QGuiApplication, QStandardItem, QStandardItemModel

    model = QStandardItemModel()
    model.appendRow(QStandardItem(""))
    data = model.mimeData([model.index(0, 0)])
    for fmt in list(data.formats()):
        data.removeFormat(fmt)
    data.setText(plain)
    if rich_html:
        data.setHtml(rich_html)
    QGuiApplication.clipboard().setMimeData(data)


def markdown_html(md: str) -> str:
    """O Markdown como HTML pelo `QTextDocument` (o que a área de transferência leva junto do texto)."""
    from PySide6.QtGui import QTextDocument

    doc = QTextDocument()
    doc.setMarkdown(md)
    return doc.toHtml()


__all__ = ["BAND_OPACITY", "BLACK", "HEALTH_TEXT", "HEALTH_TONES", "JOB_STATUS_COLOR_NAMES", "JOB_STATUS_HATCHED", "JOB_STATUS_TONES",
           "NOTICE_TONES", "QT_LOCALE", "STATE_TONES", "TRACK_MIX", "WHITE", "BarColors", "Box", "bar_colors", "clamp_to_screens",
           "contrast", "copy_rich", "fit_rect", "fit_to_screen", "health_color", "human_size", "install_translations",
           "job_status_color", "level_fraction", "luminance", "markdown_html", "mix", "state_color", "text_on"]
