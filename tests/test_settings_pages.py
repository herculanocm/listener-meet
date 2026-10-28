"""F5.4 L3: as Configurações em páginas (U-071 a U-076, U-036; A-14) e a fila (U-077, U-058). O visual se confere nos prints
do `win_probe shots`; aqui, o que se verifica sem tela: as páginas e a navegação, o erro que abre a página e marca o campo
sem caixa de mensagem, os editores tipados que regravam o texto salvo original, o momento de cada linha, os selos, a tela
sem jargão interno, o nome legível do modelo do índice e a fila com o Estado em selo e Ajuda e Fechar numa linha."""
from __future__ import annotations

import logging
import re
from typing import Any


from listener.model import JobStatus, JobStep
from listener.store import Store

JARGON = re.compile(r"\b(F\d(\.\d)?|CP\d+)\b")             # U-076: as fases e as cláusulas não aparecem na tela


def _dialog(qtbot: Any, store: Store, platform: Any, errors: dict[str, str] | None = None) -> Any:
    from listener.ui.settings_min import SettingsDialog

    dlg = SettingsDialog(store, platform, errors or {})
    qtbot.addWidget(dlg)
    return dlg


# ---------------------------------------------------------------- U-071: páginas, navegação e um Salvar
def test_settings_open_in_pages_with_navigation_and_one_save(qtbot, store, platform):
    from PySide6.QtWidgets import QDialogButtonBox

    from listener.ui.keys import KEYS_TITLE
    from listener.ui.theme import KIND_PROP, PRIMARY

    dlg = _dialog(qtbot, store, platform)
    titles = [KEYS_TITLE, "Idioma e avisos", "Captura", "Atalhos", "Processamento", "LLM", "Ao vivo", "Busca", "Log e banco de dados"]
    assert list(dlg.pages) == titles and [dlg.nav.item(i).text() for i in range(dlg.nav.count())] == titles
    assert (dlg.width(), dlg.height()) == (960, 640) and dlg.stack.currentWidget() is dlg.page(KEYS_TITLE)
    dlg.nav.setCurrentRow(titles.index("Busca"))
    assert dlg.stack.currentWidget() is dlg.page("Busca")
    boxes = dlg.findChildren(QDialogButtonBox)
    assert len(boxes) == 1                                                             # um Salvar para todas as páginas
    save = boxes[0].button(QDialogButtonBox.StandardButton.Save)
    assert save.property(KIND_PROP) == PRIMARY and save.isDefault()
    for title, page in dlg.pages.items():                                              # todo editor mora numa página
        assert page.page_title == title and page.widgetResizable()
    assert all(any(p.isAncestorOf(ed.widget) for p in dlg.pages.values()) for ed in dlg._editors.values())


def test_settings_window_fits_the_screen_when_shown(qtbot, store, platform):
    """A-17: aberta, a janela inteira cabe na área livre da tela (no notebook a 150 %, o `fit_to_screen` a encolhe)."""
    dlg = _dialog(qtbot, store, platform)
    dlg.show()
    qtbot.waitExposed(dlg)
    area = dlg.screen().availableGeometry()
    assert area.contains(dlg.frameGeometry()) or dlg.size() == dlg.minimumSize()


# ---------------------------------------------------------------- U-072: o erro na página, sem caixa de mensagem
def test_validation_error_opens_the_page_marks_the_field_and_explains_in_a_bar(qtbot, store, platform, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from listener.ui.settings_editors import IntEditor, TextEditor

    boxes: list[Any] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: boxes.append(a))
    dlg = _dialog(qtbot, store, platform)
    params = dlg.provider_tabs.tab_by_id["gemini"].params                # o erro de uma sub-aba: abre a página LLM
    params.setText("{não é json")
    dlg._save()
    assert dlg.stack.currentWidget() is dlg.page("LLM") and params.property("invalid")
    assert "parâmetros não são JSON" in dlg.error_bar.label.text() and not dlg.error_bar.isHidden() and boxes == []
    assert dlg.result() != dlg.DialogCode.Accepted
    params.setText("")
    mark = dlg.editor("hotkeys.mark")                                    # A-14: o erro de texto num campo de texto
    assert isinstance(mark, TextEditor)
    mark.edit.setText("ctrl+ç")
    dlg._save()                                                          # a marca anterior sai; a nova página abre
    assert dlg.stack.currentWidget() is dlg.page("Atalhos") and mark.edit.property("invalid") and not params.property("invalid")
    assert "tecla desconhecida" in dlg.error_bar.label.text()
    top_k = dlg.editor("rag.top_k")
    assert isinstance(top_k, IntEditor)
    top_k.spin.setValue(10)
    mark.edit.setText("ctrl+alt+shift+m")
    dlg._save()
    assert dlg.result() == dlg.DialogCode.Accepted and store.get_setting("rag.top_k") == "10" and boxes == []


def test_the_mark_leaves_when_the_value_changes_also_in_the_price_table(qtbot, store, platform):
    """Revisão da F5.4 (R-09): a marca de erro sai quando o usuário mexe no campo (campo de texto, spin, lista e a tabela de
    preços); a faixa fica com o motivo até o próximo Salvar."""
    from PySide6.QtWidgets import QTableWidgetItem

    dlg = _dialog(qtbot, store, platform)
    tab = dlg.provider_tabs.tab_by_id["gemini"]
    tab.prices.setItem(0, 1, QTableWidgetItem("abc"))
    dlg._save()
    assert dlg.stack.currentWidget() is dlg.page("LLM") and tab.prices.property("invalid")
    assert "preço inválido" in dlg.error_bar.label.text()
    tab.prices.item(0, 1).setText("1.5")
    assert tab.prices.property("invalid") is None and not dlg.error_bar.isHidden()
    for widget, change in ((dlg.editor("hotkeys.mark").widget, "setText"), (dlg.editor("rag.top_k").widget, "setValue"),
                           (dlg.editor("log.level").widget, "setCurrentIndex")):
        dlg.show_problem("motivo", widget)
        assert widget.property("invalid")
        getattr(widget, change)({"setText": "ctrl+alt+shift+k", "setValue": 9, "setCurrentIndex": 0}[change])
        assert widget.property("invalid") is None, change


# ---------------------------------------------------------------- R-04: o valor salvo que a `SPECS` recusa
def test_an_invalid_saved_value_shows_the_default_warns_on_the_page_and_saves_it(qtbot, store, platform, caplog):
    for key, raw in (("pipeline.threshold", "0.9"), ("log.level", "trace"), ("pipeline.auto_start", "talvez"),
                     ("hotkeys.mark", "ctrl+ç")):
        store.set_setting(key, raw)                                      # só um banco editado à mão chega a isso
    with caplog.at_level(logging.INFO, logger="listener.ui.settings"):
        dlg = _dialog(qtbot, store, platform)
    threshold, level, auto = (dlg.editor(k) for k in ("pipeline.threshold", "log.level", "pipeline.auto_start"))
    assert (threshold.spin.text(), level.combo.currentData(), auto.box.isChecked()) == ("0,60", "INFO", True)   # o padrão
    processing, data = dlg.page("Processamento").warning, dlg.page("Log e banco de dados").warning
    assert processing is not None and data is not None
    assert "Limiar de identificação" in processing.label.text() and "Vale o padrão (0,60), gravado ao salvar" in processing.label.text()
    assert "Processar sozinho" in processing.label.text() and "'trace'" in data.label.text()
    assert "pipeline.threshold" in caplog.text and "0.9" not in caplog.text and "trace" not in caplog.text
    mark = dlg.editor("hotkeys.mark")                                    # o campo de texto mostra o texto e a validação recusa
    assert mark.edit.text() == "ctrl+ç" and mark.saved_error is None and dlg.page("Atalhos").warning is None
    assert "tecla desconhecida" in (dlg.validation_error() or "")
    threshold.spin.setValue(0.61)
    threshold.spin.setValue(0.60)                                        # mexer e voltar ao padrão também grava o padrão
    mark.edit.setText("ctrl+alt+shift+m")
    dlg._save()
    assert dlg.result() == dlg.DialogCode.Accepted
    assert [store.get_setting(k) for k in ("pipeline.threshold", "log.level", "pipeline.auto_start")] == ["0.6", "INFO", "True"]
    assert _dialog(qtbot, store, platform).page("Processamento").warning is None          # gravado: a faixa não volta


# ---------------------------------------------------------------- U-073 e A-14: editores tipados, o mesmo texto salvo
def test_typed_editors_use_checkbox_spin_with_comma_and_lists_in_the_specs_range(qtbot, store, platform):
    from PySide6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QSpinBox

    from listener.config.defaults import SPECS

    dlg = _dialog(qtbot, store, platform)
    for key, ed in dlg._editors.items():
        spec = SPECS[key]
        if spec.choices is not None:
            combo = ed.widget
            assert isinstance(combo, QComboBox), key
            assert {combo.itemData(i) for i in range(combo.count())} == set(spec.choices), key
        elif isinstance(spec.default, bool):
            assert isinstance(ed.widget, QCheckBox) and ed.widget.text() == spec.label, key
        elif isinstance(spec.default, int | float):
            spin = ed.widget
            assert isinstance(spin, QSpinBox | QDoubleSpinBox), key
            assert (spin.minimum(), spin.maximum()) == (spec.low, spec.high), key
    threshold = dlg.editor("pipeline.threshold").widget
    assert isinstance(threshold, QDoubleSpinBox)
    assert threshold.locale().decimalPoint() == "," and threshold.textFromValue(0.65) == "0,65"   # vírgula do pt-BR
    rates = dlg.editor("capture.target_rate").widget
    assert isinstance(rates, QComboBox) and rates.itemText(0) == "8000"


def test_untouched_values_keep_the_saved_text_and_changed_ones_the_specs_text(qtbot, store, platform, caplog):
    from listener.ui.settings_editors import BoolEditor, ChoiceEditor, FloatEditor

    saved = {"pipeline.threshold": "0.60", "pipeline.auto_start": "1", "log.level": "info", "capture.chunk_seconds": "60",
             "live.enabled": "sim"}
    for key, raw in saved.items():
        store.set_setting(key, raw)
    dlg = _dialog(qtbot, store, platform)
    with caplog.at_level(logging.DEBUG, logger="listener.store.settings"):
        dlg._save()                                      # o primeiro Salvar grava os padrões que o banco ainda não tinha
    assert dlg.result() == dlg.DialogCode.Accepted
    assert {k: store.get_setting(k) for k in saved} == saved                         # nada de "0.6", "True" nem "INFO"
    assert not [line for line in caplog.text.splitlines() if "->" in line and any(f" {k}:" in line for k in saved)]
    caplog.clear()
    again = _dialog(qtbot, store, platform)
    with caplog.at_level(logging.DEBUG, logger="listener.store.settings"):
        again._save()
    assert "->" not in caplog.text                                                   # sem troca nenhuma no log (D-059)
    dlg = _dialog(qtbot, store, platform)
    threshold, auto, level, rate = (dlg.editor(k) for k in ("pipeline.threshold", "pipeline.auto_start", "log.level",
                                                            "capture.target_rate"))
    assert isinstance(threshold, FloatEditor) and isinstance(auto, BoolEditor)
    assert isinstance(level, ChoiceEditor) and isinstance(rate, ChoiceEditor)
    threshold.spin.setValue(0.7)
    threshold.spin.setValue(0.6)                                                     # mexeu e voltou: o texto de antes
    auto.box.setChecked(False)
    level.combo.setCurrentIndex(level.combo.findData("DEBUG"))
    rate.combo.setCurrentIndex(rate.combo.findData(48000))
    dlg.editor("pipeline.margin").set_raw("0.05")
    margin = dlg.editor("pipeline.margin")
    assert isinstance(margin, FloatEditor)
    margin.spin.setValue(0.07)
    dlg._save()
    assert store.get_setting("pipeline.threshold") == "0.60" and store.get_setting("pipeline.auto_start") == "False"
    assert store.get_setting("log.level") == "DEBUG" and store.get_setting("capture.target_rate") == "48000"
    assert store.get_setting("pipeline.margin") == "0.07"


def test_editor_decimals_and_text_format():
    from listener.config.defaults import SPECS
    from listener.ui.settings_editors import decimals_for

    assert decimals_for(SPECS["pipeline.threshold"], "0.6") == 2 and decimals_for(SPECS["capture.silence_rms"], "0.004") == 3
    assert decimals_for(SPECS["capture.chunk_seconds"], "60.0") == 1 and decimals_for(SPECS["llm.cost_alert_usd"], "2") == 2
    assert decimals_for(SPECS["capture.silence_rms"], "0.0035") == 4                 # o texto salvo também conta


def test_float_editor_writes_plain_numbers(qtbot):
    from listener.ui.settings_editors import FloatEditor

    cost = FloatEditor("llm.cost_alert_usd", "2.0")
    qtbot.addWidget(cost.spin)
    assert cost.raw() == "2.0"
    for value, text in ((5.0, "5"), (0.5, "0.5"), (12.25, "12.25"), (0.0, "0")):
        cost.spin.setValue(value)
        assert cost.raw() == text
    cost.set_raw("abc")                                     # ilegível (R-04): o padrão à vista, que o Salvar grava
    assert cost.saved_error and cost.raw() == "2" and cost.spin.value() == 2.0


# ---------------------------------------------------------------- U-074: o momento na página e, na linha, só o que difere
def _row_notes(page: Any) -> set[str]:
    from PySide6.QtWidgets import QLabel

    return {w.text() for w in page.body.findChildren(QLabel) if w.text() in ("aplica agora", "na próxima gravação")}


def test_each_page_says_when_it_applies_and_the_row_only_what_differs(qtbot, store, platform):
    from listener.ui.settings_general import SUBTITLES

    dlg = _dialog(qtbot, store, platform)
    capture, live = dlg.page("Captura"), dlg.page("Ao vivo")
    assert "Aplica agora" in capture.subtitle.text() and "Vale na próxima gravação" in live.subtitle.text()
    assert _row_notes(capture) == {"na próxima gravação"} and _row_notes(live) == {"aplica agora"}
    assert _row_notes(dlg.page("Processamento")) == set() and _row_notes(dlg.page("LLM")) == set()
    assert all(text.rstrip().endswith(".") for text in SUBTITLES.values())


# ---------------------------------------------------------------- U-075: selos
def test_hotkeys_show_registered_or_failed_in_a_badge(qtbot, store, platform):
    from PySide6.QtWidgets import QLabel

    from listener.ui.settings_general import HOTKEY_FAILED, HOTKEY_OK
    from listener.ui.theme import Tone

    dlg = _dialog(qtbot, store, platform, {"ctrl+alt+shift+h": "outro programa já usa este atalho"})
    assert dlg.badges["hotkeys.start"].text() == HOTKEY_OK and dlg.badges["hotkeys.start"].badge_tone == Tone.SUCCESS
    failed = dlg.badges["hotkeys.history"]
    assert failed.text() == HOTKEY_FAILED and failed.badge_tone == Tone.DANGER
    assert any("outro programa já usa este atalho" in w.text() for w in dlg.page("Atalhos").findChildren(QLabel))


# ---------------------------------------------------------------- U-076: sem jargão; o cabeçalho dos preços inteiro
def _texts(dlg: Any) -> list[str]:
    from PySide6.QtWidgets import QAbstractButton, QGroupBox, QLabel, QLineEdit, QTableWidget, QTabWidget

    out = [dlg.nav.item(i).text() for i in range(dlg.nav.count())]
    for w in dlg.findChildren(QLabel):
        out += [w.text(), w.toolTip()]
    for b in dlg.findChildren(QAbstractButton):
        out += [b.text(), b.toolTip()]
    for g in dlg.findChildren(QGroupBox):
        out.append(g.title())
    for e in dlg.findChildren(QLineEdit):
        out += [e.placeholderText(), e.toolTip()]
    for t in dlg.findChildren(QTabWidget):
        out += [t.tabText(i) for i in range(t.count())]
    for table in dlg.findChildren(QTableWidget):
        out += [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
    for ed in dlg._editors.values():
        out.append(ed.widget.toolTip())
    return [t for t in out if t]


def test_no_internal_jargon_on_screen_and_the_price_header_is_whole(qtbot, store, platform):
    dlg = _dialog(qtbot, store, platform, {"ctrl+alt+shift+h": "outro programa já usa este atalho"})
    found = [t for t in _texts(dlg) if JARGON.search(t)]
    assert found == []
    dlg.nav.setCurrentRow(list(dlg.pages).index("LLM"))
    dlg.show()
    qtbot.waitExposed(dlg)
    for tab in dlg.provider_tabs.tab_by_id.values():
        header = tab.prices.horizontalHeader()
        fm = header.fontMetrics()
        texts = [tab.prices.horizontalHeaderItem(c).text() for c in range(tab.prices.columnCount())]
        assert header.defaultAlignment() & tab.prices.horizontalHeaderItem(0).textAlignment() is not None
        assert tab.prices.verticalHeader().isHidden()
        if tab.isVisible():
            assert all(header.sectionSize(c) >= fm.horizontalAdvance(t) for c, t in enumerate(texts)), texts


# ---------------------------------------------------------------- U-036: o índice com o nome do modelo, sem mandar para cá
def test_index_status_names_the_model_and_does_not_send_to_settings(qtbot, store, platform, tmp_path):
    from listener.ui.settings_search import here, model_name

    assert model_name("local/multilingual-e5-small@384") == "e5-small, local"
    assert model_name("gemini/gemini-embedding-001@768") == "gemini-embedding-001, Gemini" and model_name("x") == "x"
    assert here("busca local não baixada: prepare em Configurações (Busca)") == "busca local não baixada: prepare nesta página"
    assert here("sem chave (Configurações, Chaves de acesso)") == "sem chave (página Chaves de acesso)"
    store.set_setting("pipeline.models_dir", str(tmp_path / "modelos"))      # sem o e5 baixado
    store.providers.ensure_local()
    store.providers.set_role("embed", "local")
    dlg = _dialog(qtbot, store, platform)
    status = dlg.index_status.text()
    assert "vetores de e5-small, local" in status and "local/multilingual" not in status
    assert "Configurações" not in status and "Configurações" not in dlg.local_status.text()
    assert dlg.local_row.badge.text() in ("Não baixada", "Indisponível")


# ---------------------------------------------------------------- U-077 e U-058: a fila
def _job(store: Store, status: str) -> None:
    mid = store.create_meeting("Comitê de investimentos do fundo", "teams", None, None)
    store.finish_meeting(mid, 600_000)
    jid = store.jobs.enqueue(mid)
    if status == JobStatus.RUNNING:
        store.jobs.claim(pid=1)
        store.jobs.set_progress(jid, JobStep.DIARIZE, 0.4)


def test_queue_layout_badges_and_help_and_close_in_one_row(qtbot, store):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QHeaderView

    from listener.ui.components import BadgeDelegate
    from listener.ui.queue import DETAIL_COL, MEETING_COL, STATE_COL, STATUS_BADGES, QueueDialog
    from listener.ui.theme import STATUS_TONES, TONE_ROLE, Tone

    assert set(STATUS_BADGES) == set(JobStatus) and set(STATUS_BADGES.values()) <= set(STATUS_TONES)
    _job(store, JobStatus.RUNNING)
    dlg = QueueDialog(store)
    qtbot.addWidget(dlg)
    dlg._timer.stop()
    assert (dlg.width(), dlg.height()) == (1040, 480)
    assert isinstance(dlg.table.itemDelegateForColumn(STATE_COL), BadgeDelegate)
    state = dlg.table.item(0, STATE_COL)
    assert state.text() == "rodando" and state.data(TONE_ROLE) == Tone.INFO.value          # o texto fica para o leitor
    header = dlg.table.horizontalHeader()
    assert header.sectionResizeMode(MEETING_COL) == QHeaderView.ResizeMode.Stretch and not header.stretchLastSection()
    assert header.defaultAlignment() & Qt.AlignmentFlag.AlignLeft and dlg.table.verticalHeader().isHidden()
    assert dlg.table.item(0, MEETING_COL).toolTip().startswith("Comitê de investimentos do fundo")
    assert dlg.table.item(0, DETAIL_COL).toolTip() == ""
    assert dlg._message_bar is None                                                   # a faixa só nasce com um aviso
    dlg.show()
    qtbot.waitExposed(dlg)
    actions = (dlg.btn_pause, dlg.btn_cancel, dlg.btn_retry, dlg.btn_reprocess, dlg.btn_review, dlg.btn_open)
    assert {b.geometry().top() for b in (*actions, dlg.btn_help, dlg.btn_close)} == {dlg.btn_pause.geometry().top()}
    assert max(b.x() for b in actions) < dlg.btn_help.x() < dlg.btn_close.x() and dlg.btn_close.text() == "Fechar"
    dlg.btn_close.click()
    assert not dlg.isVisible() and dlg.result() == dlg.DialogCode.Rejected


def test_queue_reprocess_result_goes_to_a_bar(qtbot, store):
    from listener.ui.queue import QueueDialog

    _job(store, JobStatus.RUNNING)
    dlg = QueueDialog(store)
    qtbot.addWidget(dlg)
    dlg._timer.stop()
    dlg.table.selectRow(0)
    assert dlg.reprocess_selected() is None                                          # já tem um processamento ativo
    bar = dlg._message_bar
    assert bar is not None and not bar.isHidden() and bar.kind == "warning" and "andamento" in dlg.message.text()
