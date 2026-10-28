"""Utilidades da interface: níveis em dB, cores de saúde, formatação; F5: área de transferência com texto e HTML; a
tradução pt-BR dos textos padrão do Qt. F5.3 (U-047, A-08): as cores da barra da fila por estado e o contraste do texto
sobre elas (WCAG 2.1, 4,5:1), nos temas claro e escuro; (U-048) as do aviso que some sozinho, por tipo."""
from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

from ..model import Health, JobStatus

if TYPE_CHECKING:
    from PySide6.QtCore import QCoreApplication, QTranslator

QT_LOCALE = "pt_BR"             # a interface é em pt-BR (i18n antes da F6): os botões padrão do Qt seguem o mesmo idioma

HEALTH_COLORS: dict[str, str] = {Health.OK: "#2ecc71", Health.WARN: "#f1c40f", Health.ALERT: "#e74c3c", Health.MUTED: "#7f8c8d",
                                 Health.PAUSED: "#95a5a6", Health.OFF: "#34495e", Health.LOST: "#e74c3c"}
HEALTH_TEXT: dict[str, str] = {Health.OK: "ok", Health.WARN: "sem sinal", Health.ALERT: "MUDO", Health.MUTED: "mudo",
                               Health.PAUSED: "pausado", Health.OFF: "desligado", Health.LOST: "dispositivo perdido"}

# F5.3 (A-08): a barra da fila na cor do estado, a mesma nos dois temas (tons médios aparecem no trilho claro e no escuro);
# o cancelado é hachurado, porque um cinza-escuro some no tema escuro. A próxima fase (design) junta a paleta inteira aqui.
JOB_STATUS_COLORS: dict[str, str] = {JobStatus.PENDING: "#95a5a6", JobStatus.RUNNING: "#3498db", JobStatus.PAUSED: "#f39c12",
                                     JobStatus.DONE: "#2ecc71", JobStatus.ERROR: "#e74c3c", JobStatus.CANCELLED: "#bdc3c7"}
JOB_STATUS_HATCH: dict[str, str] = {JobStatus.CANCELLED: "#7f8c8d"}
JOB_STATUS_COLOR_NAMES: dict[str, str] = {JobStatus.PENDING: "barra cinza", JobStatus.RUNNING: "barra azul, com uma faixa que anda",
                                          JobStatus.PAUSED: "barra âmbar", JobStatus.DONE: "barra verde", JobStatus.ERROR: "barra vermelha",
                                          JobStatus.CANCELLED: "barra listrada"}
BLACK, WHITE = "#000000", "#ffffff"
# F5.3 (U-048, A-14): o aviso que some sozinho, com a faixa na cor do tipo (as chaves são os valores de `ui.toast.NoticeKind`),
# as mesmas da barra da fila: verde pronto, azul informação, vermelho erro. Fundo, borda e textos são os do widget de
# gravação; o texto fica claro sobre o fundo (contraste de 4,5:1 ou mais) e a cor do tipo vai só na faixa (3:1 basta).
NOTICE_COLORS: dict[str, str] = {"pronto": JOB_STATUS_COLORS[JobStatus.DONE], "informação": JOB_STATUS_COLORS[JobStatus.RUNNING],
                                 "erro": JOB_STATUS_COLORS[JobStatus.ERROR]}
NOTICE_BACKGROUND, NOTICE_BORDER, NOTICE_TEXT, NOTICE_MUTED = "#1c1e24", "#555555", "#eeeeee", "#aaaaaa"
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


@lru_cache(maxsize=64)
def bar_colors(status: str, base: str, text: str) -> BarColors:
    """As cores da barra de um job no estado `status` sobre a tabela de base `base` e texto `text` (cores da palette(),
    "#rrggbb"); um estado desconhecido sai como pendente. Em cache: a faixa do "rodando" repinta a coluna a cada 120 ms."""
    fill = JOB_STATUS_COLORS.get(status, JOB_STATUS_COLORS[JobStatus.PENDING])
    track = mix(base, text, TRACK_MIX)
    on_fill, on_track = text_on(fill), text_on(track)
    return BarColors(track, fill, on_fill, on_track, WHITE if on_fill == BLACK else BLACK, WHITE if on_track == BLACK else BLACK,
                     JOB_STATUS_HATCH.get(status))


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
