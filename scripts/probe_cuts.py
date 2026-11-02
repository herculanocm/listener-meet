"""Os relatórios `cortados` e `pequenos` das fotos do `win_probe shots` (F5.4 L1, A-21), saídos do `scripts/probe_ui.py`,
que passava de 400 linhas (F5.11, A-35). Não fazem parte do produto.

cortados   rótulo, botão, aba, cabeçalho de tabela ou título de cartão (L1b) com o texto maior que o espaço. F5.11 (A-16):
           também o combo, a célula de tabela à vista e o rótulo cortado com reticências e o texto inteiro na dica (antes,
           "de propósito", não contava: o alerta do widget e a etapa da fila cortavam assim). Ficam de fora o corte no meio
           com o texto inteiro na dica (o nome de um aparelho, U-110, que corta assim por desenho), a célula cortada com o
           texto inteiro na dica e a legenda do widget (a última fala numa linha; a inteira fica na janela Ao vivo). O botão
           é medido pelo que ele mostra (o texto, o ícone, as margens e a seta só com menu): o `sizeHint` do estilo do Windows
           tem a largura mínima de botão (80 px) e acusava o "⋯" da reunião, inteiro na tela.
pequenos   o alvo de clique do Aceite 3: botão, campo e combo abaixo de 32 por 32 px; caixa de seleção abaixo de 24 px."""
from __future__ import annotations

from typing import Any

TARGET_PX, CHECK_PX = 32, 24          # Aceite 3: botão, campo e combo; caixa de seleção
CARD_TITLE_INSET = 6                  # a folga à direita do título do cartão (o `TITLE_INSET` de `ui/style.py`)
ELLIPSIS = "\N{HORIZONTAL ELLIPSIS}"
BY_DESIGN = frozenset({"CaptionLine"})   # a legenda do widget: a última fala numa linha, cortada por desenho
CELL_ROWS = 60                        # as células das primeiras linhas à vista bastam (a fila e as tabelas das abas)


def _name(w: Any) -> str:
    text = ""
    for attr in ("text", "title", "windowTitle", "currentText"):
        fn = getattr(w, attr, None)
        if callable(fn):
            text = str(fn() or "")
            if text:
                break
    text = " ".join(text.split())
    return f"{type(w).__name__}{'#' + w.objectName() if w.objectName() else ''} {text[:48]!r}"


def _internal(w: Any) -> bool:
    """Peças internas do Qt (setas de rolagem das abas, o botão de limpar do campo, o campo de um combo editável)."""
    from PySide6.QtWidgets import QAbstractSpinBox, QComboBox, QLineEdit, QTabBar

    parent = w.parentWidget()
    return w.objectName().startswith("qt_") or isinstance(parent, QTabBar | QLineEdit | QComboBox | QAbstractSpinBox)


def middle_cut(shown: str, full: str) -> bool:
    """O corte no meio com o texto inteiro à mão (o começo e o fim à vista): por desenho, não conta."""
    head, sep, tail = shown.partition(ELLIPSIS)
    return bool(sep and head and tail) and full.startswith(head) and full.endswith(tail)


def _elided_with_tip(text: str, tip: str) -> bool:
    """A-16: o rótulo cortado no fim, com o inteiro na dica (a `ElidedLabel` do alerta, a coluna de uma tabela)."""
    shown = " ".join(text.split())
    full = " ".join(tip.split())
    if not shown.endswith(ELLIPSIS) or not full or middle_cut(shown, full):
        return False
    return full.startswith(shown.removesuffix(ELLIPSIS).rstrip()) and len(full) > len(shown)


def _label_cut(lab: Any) -> dict[str, Any] | None:
    pix = lab.pixmap()
    text = lab.text()
    if (pix is not None and not pix.isNull()) or not text.strip() or type(lab).__name__ in BY_DESIGN:
        return None
    if _elided_with_tip(text, lab.toolTip()):
        return {"cortado_com_dica": " ".join(lab.toolTip().split())[:64]}
    if lab.wordWrap():
        need = lab.heightForWidth(lab.width())
        return {"need_h": need, "h": lab.height()} if need > lab.height() + 1 else None
    need = lab.sizeHint().width()
    return {"need_w": need, "w": lab.width()} if need > lab.width() + 1 else None


def _button_need(btn: Any) -> int:
    """A largura que o conteúdo do `QPushButton` pede: o texto, o ícone, a seta (só com menu) e as margens do estilo; o
    botão que mede a si mesmo (o `KeyButton` da revisão, com o selo da tecla), a caixa e o botão de ferramenta respondem
    pelo `sizeHint` (eles não têm a largura mínima do botão)."""
    from PySide6.QtWidgets import QPushButton, QStyle

    custom = any("sizeHint" in vars(k) for k in type(btn).__mro__ if not k.__module__.startswith(("PySide6", "builtins")))
    if custom or not isinstance(btn, QPushButton):
        return int(btn.sizeHint().width())
    style = btn.style()
    menu = getattr(btn, "menu", None)
    arrow = style.pixelMetric(QStyle.PixelMetric.PM_MenuButtonIndicator, None, btn) if callable(menu) and menu() else 0
    icon = btn.iconSize().width() + 4 if not btn.icon().isNull() else 0
    pad = 2 * (style.pixelMetric(QStyle.PixelMetric.PM_ButtonMargin, None, btn) +
               style.pixelMetric(QStyle.PixelMetric.PM_DefaultFrameWidth, None, btn))
    return int(btn.fontMetrics().horizontalAdvance(btn.text().replace("&", "")) + icon + arrow + pad)


def _button_cut(btn: Any) -> dict[str, Any] | None:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QToolButton

    if not btn.text() or (isinstance(btn, QToolButton) and btn.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly):
        return None
    need = _button_need(btn)
    return {"need_w": need, "w": btn.width()} if need > btn.width() + 1 else None


def _combo_cut(combo: Any) -> dict[str, Any] | None:
    """A-16: o texto do item atual no campo do combo; o `ElidedCombo` (o microfone) corta no meio, com o nome na dica."""
    from PySide6.QtWidgets import QStyle, QStyleOptionComboBox

    text = combo.currentText()
    shown = getattr(combo, "shown_text", None)
    if not text or (callable(shown) and middle_cut(shown(), text) and combo.toolTip() == text):
        return None
    opt = QStyleOptionComboBox()
    combo.initStyleOption(opt)
    field = combo.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, opt, QStyle.SubControl.SC_ComboBoxEditField, combo)
    icon = opt.iconSize.width() + 4 if not opt.currentIcon.isNull() else 0
    need = combo.fontMetrics().horizontalAdvance(text) + icon
    return {"need_w": need, "w": field.width()} if need > field.width() + 1 else None


def _plain(tip: str) -> str:
    """A dica sem o HTML (o `convertFromPlainText` do Histórico põe o título num parágrafo)."""
    from PySide6 import QtGui

    text = QtGui.QTextDocumentFragment.fromHtml(tip).toPlainText() if QtGui.Qt.mightBeRichText(tip) else tip
    return " ".join(text.split())


def _cells_cut(view: Any) -> list[dict[str, Any]]:
    """A-16: as células à vista de uma tabela cujo texto não cabe na coluna e não está inteiro na dica (a célula de texto
    livre, cortada com a dica inteira, não conta)."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QStyle

    model = view.model()
    if model is None:
        return []
    pad = 2 * (view.style().pixelMetric(QStyle.PixelMetric.PM_FocusFrameHMargin, None, view) + 1)
    area, fm, found = view.viewport().rect(), view.fontMetrics(), []
    for row in range(min(model.rowCount(), CELL_ROWS)):
        for col in range(model.columnCount()):
            index = model.index(row, col)
            text = str(model.data(index, Qt.ItemDataRole.DisplayRole) or "")
            rect = view.visualRect(index)
            if not text or view.isColumnHidden(col) or not area.intersects(rect):
                continue
            need = max(fm.horizontalAdvance(line) for line in text.split("\n")) + pad
            tip = _plain(str(model.data(index, Qt.ItemDataRole.ToolTipRole) or ""))
            if need > rect.width() + 1 and " ".join(text.split()) not in tip:
                found.append({"what": f"célula {row},{col} {text[:32]!r}", "need_w": need, "w": rect.width()})
    return found


def _tabs_cut(bar: Any) -> list[dict[str, Any]]:
    from PySide6.QtWidgets import QStyle

    space = bar.style().pixelMetric(QStyle.PixelMetric.PM_TabBarTabHSpace, None, bar)
    found = []
    for i in range(bar.count()):
        if not bar.isTabVisible(i):
            continue
        rect, text = bar.tabRect(i), bar.tabText(i)
        need = bar.fontMetrics().horizontalAdvance(text) + space
        if need > rect.width() + 1 or not bar.rect().contains(rect):
            found.append({"what": f"aba {text!r}", "need_w": need, "w": rect.width(), "inside": bar.rect().contains(rect)})
    return found


def _header_cut(header: Any) -> list[dict[str, Any]]:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QStyle

    model = header.model()
    if model is None or header.orientation() != Qt.Orientation.Horizontal:
        return []
    style, fm = header.style(), header.fontMetrics()
    pad = 2 * style.pixelMetric(QStyle.PixelMetric.PM_HeaderMargin, None, header)
    mark = style.pixelMetric(QStyle.PixelMetric.PM_HeaderMarkSize, None, header)
    found = []
    for logical in range(header.count()):
        if header.isSectionHidden(logical):
            continue
        text = str(model.headerData(logical, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) or "")
        need = max(fm.horizontalAdvance(line) for line in text.split("\n")) + pad
        if header.isSortIndicatorShown() and header.sortIndicatorSection() == logical:
            need += mark
        if text and need > header.sectionSize(logical) + 1:
            found.append({"what": f"cabeçalho {text!r}", "need_w": need, "w": header.sectionSize(logical)})
    return found


def _group_cut(box: Any) -> dict[str, Any] | None:
    """F5.4 L1b: o título do cartão (o `ThemeStyle` o pinta dentro, `TITLE_INSET` abaixo) no espaço que o grupo tem."""
    from PySide6.QtWidgets import QStyle, QStyleOptionGroupBox

    if not box.title():
        return None
    opt = QStyleOptionGroupBox()
    box.initStyleOption(opt)
    label = box.style().subControlRect(QStyle.ComplexControl.CC_GroupBox, opt, QStyle.SubControl.SC_GroupBoxLabel, box)
    need = label.left() + box.fontMetrics().horizontalAdvance(box.title()) + CARD_TITLE_INSET
    return {"need_w": need, "w": box.width()} if need > box.width() + 1 else None


def _cut(w: Any) -> list[dict[str, Any]]:
    from PySide6.QtWidgets import QAbstractButton, QComboBox, QGroupBox, QHeaderView, QLabel, QTableView, QTabBar

    if isinstance(w, QLabel):
        found = _label_cut(w)
    elif isinstance(w, QAbstractButton):
        found = _button_cut(w)
    elif isinstance(w, QGroupBox):
        found = _group_cut(w)
    elif isinstance(w, QComboBox):
        found = _combo_cut(w)
    elif isinstance(w, QTabBar | QHeaderView | QTableView):
        items = _tabs_cut(w) if isinstance(w, QTabBar) else _header_cut(w) if isinstance(w, QHeaderView) else _cells_cut(w)
        return [{"widget": _name(w), **item} for item in items]
    else:
        return []
    return [{"widget": _name(w), **found}] if found else []


def _small(w: Any) -> dict[str, Any] | None:
    from PySide6.QtWidgets import QAbstractSpinBox, QCheckBox, QComboBox, QLineEdit, QPushButton, QRadioButton, QToolButton

    size = {"w": w.width(), "h": w.height()}
    if isinstance(w, QCheckBox | QRadioButton):
        return {"widget": _name(w), "min": f"altura {CHECK_PX}", **size} if w.height() < CHECK_PX else None
    if isinstance(w, QPushButton | QToolButton):
        small = w.height() < TARGET_PX or w.width() < TARGET_PX
        return {"widget": _name(w), "min": f"{TARGET_PX} x {TARGET_PX}", **size} if small else None
    if isinstance(w, QLineEdit | QComboBox | QAbstractSpinBox):
        return {"widget": _name(w), "min": f"altura {TARGET_PX}", **size} if w.height() < TARGET_PX else None
    return None


def layout_report(root: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Os `cortados` e os `pequenos` do que está à vista na janela (abas escondidas e janelas fechadas ficam fora)."""
    from PySide6.QtWidgets import QWidget

    cut: list[dict[str, Any]] = []
    small: list[dict[str, Any]] = []
    for w in (root, *root.findChildren(QWidget)):
        if (w is not root and not w.isVisibleTo(root)) or w.width() <= 0 or _internal(w):
            continue
        cut.extend(_cut(w))
        found = _small(w)
        if found is not None:
            small.append(found)
    return cut, small


__all__ = ["CHECK_PX", "ELLIPSIS", "TARGET_PX", "layout_report", "middle_cut"]
