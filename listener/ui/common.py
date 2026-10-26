"""Utilidades da interface: níveis em dB, cores de saúde, formatação; F5: área de transferência com texto e HTML; a
tradução pt-BR dos textos padrão do Qt."""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

from ..model import Health

if TYPE_CHECKING:
    from PySide6.QtCore import QCoreApplication, QTranslator

QT_LOCALE = "pt_BR"             # a interface é em pt-BR (i18n antes da F6): os botões padrão do Qt seguem o mesmo idioma

HEALTH_COLORS: dict[str, str] = {Health.OK: "#2ecc71", Health.WARN: "#f1c40f", Health.ALERT: "#e74c3c", Health.MUTED: "#7f8c8d",
                                 Health.PAUSED: "#95a5a6", Health.OFF: "#34495e", Health.LOST: "#e74c3c"}
HEALTH_TEXT: dict[str, str] = {Health.OK: "ok", Health.WARN: "sem sinal", Health.ALERT: "MUDO", Health.MUTED: "mudo",
                               Health.PAUSED: "pausado", Health.OFF: "desligado", Health.LOST: "dispositivo perdido"}


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
