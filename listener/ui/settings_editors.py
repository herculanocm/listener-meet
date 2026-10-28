"""Os editores tipados das Configurações (F5.4 L3, U-073; A-14): sim/não em caixa de seleção, número num spin com a vírgula
do pt-BR e a faixa da `SPECS`, as opções (taxas de amostragem, modelos, nível de log, idioma) em lista e o resto num campo
de texto. A validação continua a da `SPECS` (CP8), sobre o texto que cada editor devolve.

- `raw()`: o texto que vai para o banco. Enquanto o valor à vista for o mesmo que o editor mostrou ao abrir, é o texto salvo
  original, tal como estava (sem trocar "0.60" por "0.6", "True" por "1" nem "info" por "INFO", que virariam troca no log
  da D-059); mexido, o valor novo no texto que a `SPECS` lê ("0.65", "True", "16000").
- `set_raw(texto)`: um texto como se viesse do banco (os testes põem valores por aqui). Revisão da F5.4 (R-04): num editor
  tipado (número, lista, sim/não), um texto que a `SPECS` recusa (fora da faixa ou ilegível, só de um banco editado à mão)
  fica em `saved_error`, o editor mostra o padrão e `raw()` devolve o valor à vista: salvar grava o padrão (antes o Salvar
  ficava travado, e mexer e voltar ao padrão não destravava). A página avisa numa faixa (`ui/settings_min.py`). O campo de
  texto (atalhos, caminhos) segue mostrando o texto como está, e a validação o recusa.
- Sem sinais: comparar o valor à vista com o da abertura não liga nada no C++ (R-06). A roda do mouse só muda spin e lista
  com o foco neles: rolando a página por cima deles, o valor não muda sozinho.
- Largura: spin e lista com pelo menos `COMPACT_W` (alinhados na coluna); o spin com o maior texto da faixa mais as setas
  ([fato, 28/10] o `windows11` põe as duas setas lado a lado e o `sizeHint` do spin não conta com elas: "0,004" saía cortado)."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any, override

from PySide6.QtCore import QLocale, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QLineEdit, QSpinBox, QWidget

from ..config.defaults import LOG_LEVELS, SPECS, SettingSpec
from .common import QT_LOCALE

DECIMALS: dict[str, int] = {"llm.cost_alert_usd": 2}             # dinheiro, com os centavos
COMPACT_W = 120                                                   # spin e lista: a mesma largura mínima, alinhados
SPIN_CHROME = 80                                                  # as duas setas lado a lado do `windows11` e a margem
MAX_DECIMALS = 4
INT_LIMIT = 2**31 - 1
CHOICE_ORDER: dict[str, tuple[Any, ...]] = {"log.level": LOG_LEVELS}   # a ordem de sempre; o resto, em ordem crescente


class _Spin(QSpinBox):
    """Spin inteiro que só muda pela roda com o foco (rolar a página por cima dele não troca o valor)."""

    @override
    def wheelEvent(self, event: QWheelEvent) -> None:
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class _DoubleSpin(QDoubleSpinBox):
    @override
    def wheelEvent(self, event: QWheelEvent) -> None:
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class _Combo(QComboBox):
    @override
    def wheelEvent(self, event: QWheelEvent) -> None:
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


def _compact(widget: QSpinBox | QDoubleSpinBox | QComboBox, longest: str = "") -> None:
    """A largura mínima do spin e da lista: `COMPACT_W`, ou o maior texto da faixa com as setas."""
    need = widget.fontMetrics().horizontalAdvance(longest) + SPIN_CHROME if longest else 0
    widget.setMinimumWidth(max(COMPACT_W, need))


def _longest(spin: QSpinBox | QDoubleSpinBox) -> str:
    texts = (spin.textFromValue(spin.minimum()), spin.textFromValue(spin.maximum()))   # type: ignore[arg-type]
    return max(texts, key=len)


class SettingEditor:
    """A base: a chave, a linha da `SPECS`, o widget, o texto salvo (`saved`) e o valor que o widget mostrou com ele
    (`shown`). Cada tipo diz como ler o valor (`value`), como escrevê-lo em texto (`format`) e como mostrar um texto
    (`show`)."""
    widget: QWidget

    typed = True                    # número, lista e sim/não: o texto salvo que a `SPECS` recusa dá lugar ao padrão (R-04)

    def __init__(self, key: str, raw: str) -> None:
        self.key = key
        self.spec: SettingSpec[Any] = SPECS[key]
        self.saved = raw
        self.saved_error: str | None = None
        self.shown: object = None

    def raw(self) -> str:
        value = self.value()
        return self.saved if value == self.shown and self.saved_error is None else self.format(value)

    def set_raw(self, raw: str) -> None:
        self.saved = raw
        self.saved_error = self.spec.error(raw) if self.typed else None
        self.show(raw)
        self.shown = self.value()

    def shown_text(self) -> str:
        """O valor à vista como a tela o escreve ("0,60", "INFO", "marcado")."""
        raise NotImplementedError

    def parsed(self, raw: str) -> Any:
        """O valor de um texto pela `SPECS`; o padrão quando o texto não vale (o editor mostra o que o app usa)."""
        try:
            return self.spec.value(raw)
        except (ValueError, TypeError):
            return self.spec.default

    def value(self) -> object:
        raise NotImplementedError

    def format(self, value: Any) -> str:
        return str(value)

    def show(self, raw: str) -> None:
        raise NotImplementedError


class TextEditor(SettingEditor):
    """Campo de texto (atalhos, glossário, pastas, proxy); `fallback` é o que se mostra com o texto salvo vazio (o
    arquivo de log padrão), sem gravar nada enquanto o usuário não mexe. O texto salvo inválido fica à vista para a
    validação recusar (o usuário o corrige)."""
    typed = False

    def __init__(self, key: str, raw: str, fallback: str = "") -> None:
        super().__init__(key, raw)
        self.fallback = fallback
        self.edit = QLineEdit()
        self.widget = self.edit
        self.set_raw(raw)

    @override
    def value(self) -> str:
        return self.edit.text()

    @override
    def format(self, value: Any) -> str:
        return str(value).strip()

    @override
    def show(self, raw: str) -> None:
        self.edit.setText(raw or self.fallback)

    @override
    def shown_text(self) -> str:
        return self.edit.text()


class BoolEditor(SettingEditor):
    """Sim/não numa caixa de seleção com o rótulo da `SPECS` (U-073); mexido, grava "True" ou "False" (o `str` do bool, como
    o padrão)."""

    def __init__(self, key: str, raw: str) -> None:
        super().__init__(key, raw)
        self.box = QCheckBox(self.spec.label)
        self.widget = self.box
        self.set_raw(raw)

    @override
    def value(self) -> bool:
        return self.box.isChecked()

    @override
    def show(self, raw: str) -> None:
        self.box.setChecked(bool(self.parsed(raw)))

    @override
    def shown_text(self) -> str:
        return "marcado" if self.box.isChecked() else "desmarcado"


class IntEditor(SettingEditor):
    """Número inteiro num spin na faixa da `SPECS`."""

    def __init__(self, key: str, raw: str) -> None:
        super().__init__(key, raw)
        self.spin = _Spin()
        self.spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.spin.setLocale(QLocale(QT_LOCALE))
        low, high = self.spec.low, self.spec.high
        self.spin.setRange(int(low) if low is not None else -INT_LIMIT, int(high) if high is not None else INT_LIMIT)
        _compact(self.spin, _longest(self.spin))
        self.widget = self.spin
        self.set_raw(raw)

    @override
    def value(self) -> int:
        return self.spin.value()

    @override
    def show(self, raw: str) -> None:
        self.spin.setValue(int(self.parsed(raw)))

    @override
    def shown_text(self) -> str:
        return self.spin.text()


def decimals_for(spec: SettingSpec[Any], raw: str) -> int:
    """As casas do spin: as que a faixa, o padrão e o texto salvo pedem (de 1 a `MAX_DECIMALS`; o dinheiro, 2)."""
    if spec.key in DECIMALS:
        return DECIMALS[spec.key]
    places = 1
    for value in (spec.low, spec.high, spec.default, raw.strip()):
        text = str(value) if value is not None else ""
        if "." in text and "e" not in text.lower():
            places = max(places, len(text.split(".", 1)[1].rstrip("0")))
    return min(places, MAX_DECIMALS)


class FloatEditor(SettingEditor):
    """Número com vírgula (`pt_BR`) num spin na faixa da `SPECS`, nas casas que ela pede; mexido, grava com ponto e sem
    zeros à direita ("0.65", "60"), o que o `float` lê."""

    def __init__(self, key: str, raw: str) -> None:
        super().__init__(key, raw)
        self.decimals = decimals_for(self.spec, raw)
        self.spin = _DoubleSpin()
        self.spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.spin.setLocale(QLocale(QT_LOCALE))
        self.spin.setDecimals(self.decimals)
        low = float(self.spec.low) if self.spec.low is not None else 0.0
        high = float(self.spec.high) if self.spec.high is not None else float(INT_LIMIT)
        self.spin.setRange(low, high)
        self.spin.setSingleStep(10.0 ** -self.decimals if high - low <= 1 else 1.0)
        _compact(self.spin, _longest(self.spin))
        self.widget = self.spin
        self.set_raw(raw)

    @override
    def value(self) -> float:
        return round(self.spin.value(), self.decimals)

    @override
    def format(self, value: Any) -> str:
        return f"{float(value):.{self.decimals}f}".rstrip("0").rstrip(".")

    @override
    def show(self, raw: str) -> None:
        self.spin.setValue(float(self.parsed(raw)))

    @override
    def shown_text(self) -> str:
        return self.spin.text()


def choice_items(spec: SettingSpec[Any]) -> list[tuple[str, object]]:
    """As opções da `SPECS` em lista (o texto e o valor): a ordem de `CHOICE_ORDER` ou a crescente."""
    choices = spec.choices or frozenset()
    order = CHOICE_ORDER.get(spec.key) or tuple(sorted(choices))
    return [(str(c), c) for c in order if c in choices]


class ChoiceEditor(SettingEditor):
    """As opções numa lista (U-073: a taxa em lista); `items` (texto, valor) troca o texto mostrado (o idioma)."""

    def __init__(self, key: str, raw: str, items: Sequence[tuple[str, object]] | None = None) -> None:
        super().__init__(key, raw)
        self.combo = _Combo()
        self.combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        for text, data in items if items is not None else choice_items(self.spec):
            self.combo.addItem(text, data)
        _compact(self.combo)
        self.widget = self.combo
        self.set_raw(raw)

    @override
    def value(self) -> object:
        return self.combo.currentData()

    @override
    def show(self, raw: str) -> None:
        self.combo.setCurrentIndex(max(0, self.combo.findData(self.parsed(raw))))

    @override
    def shown_text(self) -> str:
        return self.combo.currentText()


def make_editor(key: str, raw: str) -> SettingEditor:
    """O editor pelo tipo da chave na `SPECS`: opções em lista, bool em caixa, int e float em spin, o resto em texto."""
    spec = SPECS[key]
    if spec.choices is not None:
        return ChoiceEditor(key, raw)
    if isinstance(spec.default, bool):
        return BoolEditor(key, raw)
    if isinstance(spec.default, int):
        return IntEditor(key, raw)
    if isinstance(spec.default, float):
        return FloatEditor(key, raw)
    return TextEditor(key, raw)


__all__ = ["BoolEditor", "ChoiceEditor", "FloatEditor", "IntEditor", "SettingEditor", "TextEditor", "choice_items", "decimals_for",
           "make_editor"]
