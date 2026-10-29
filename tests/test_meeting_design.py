"""F5.4 L4 (U-034, U-035, U-059, U-078 a U-088; A-15, A-17, A-21): a reunião aberta, o Histórico, a revisão e a ficha, o
assistente e o Ao vivo no sistema de design. As vozes numeradas por reunião só na tela (os rótulos internos seguem na
leitura do pipeline, na busca e na exportação); a fala com o tempo em legenda, o ponto na cor da faixa e o nome em negrito;
a linha do tempo com o cursor em `accent` e o marcador em `text`; a ata lida formatada e editada pelo Markdown, com o estado
vazio e a ação; as ações com o prazo vencido em selo; o título sem moldura até o foco; o player com ícones; o Histórico com a
lupa, o índice na linha das abas e o Estado em selo; o HTML sem cor fixa; a revisão em cartões com a tecla em selo e o
primário da etapa; a ficha com o apagar longe do Salvar; o assistente no `ModernStyle` com o passo."""
from __future__ import annotations

import re
from typing import Any

import pytest
from PySide6.QtCore import QModelIndex, QPoint, Qt
from PySide6.QtGui import QColor

from listener.model import ActionStatus, ClusterStatus, JobStep, SummaryKind
from listener.pipeline import review
from listener.store.actions import ActionInsert
from listener.store.summaries import SummaryInsert
from listener.ui import icons
from listener.ui.theme import DARK, KIND_PROP, LIGHT, PRIMARY, TONE_ROLE, Tone, html_css
from tests.fake_player import FakeOutputs
from tests.test_rag_index import _meeting, _people, _pipeline_job
from tests.test_review import _processed

pytest.importorskip("pytestqt")

HEX = re.compile(r"#[0-9a-f]{3,8}\b|color\s*:", re.IGNORECASE)
MINUTES_MD = "## Decisões\n\n- Aprovar a **auditoria** até sexta [01:10]\n\n## Próximos passos\n\n- Mandar a planilha\n"


@pytest.fixture
def platform(tmp_path, clock):
    from tests.fake_platform import FakePlatform

    return FakePlatform(tmp_path, clock=clock, r_format=(16000, 1), system_format=(16000, 1))


def near(color: QColor, expected: str, tol: int = 16) -> bool:
    e = QColor(expected)
    return all(abs(a - b) <= tol for a, b in ((color.red(), e.red()), (color.green(), e.green()), (color.blue(), e.blue())))


def _minutes(store: Any, mid: int, job: int, text: str = MINUTES_MD) -> int:
    return store.summaries.insert(SummaryInsert(mid, SummaryKind.MINUTES, "{}", text, "gemini", "m", "minutes/1.0", job, None, "pt-BR"))


# ---------------------------------------------------------------- U-035 e F5.5: as vozes numeradas
def test_voices_are_numbered_by_first_speech_and_a_merge_takes_the_target_number():
    from listener.pipeline.voices import number_voices

    clusters = [{"id": 10, "channel": "R", "cluster": "SPEAKER_03", "merged_into": None},
                {"id": 11, "channel": "R", "cluster": "SPEAKER_00", "merged_into": None},
                {"id": 12, "channel": "R", "cluster": "SPEAKER_01", "merged_into": 10},
                {"id": 13, "channel": "R", "cluster": "SPEAKER_02", "merged_into": None},      # ainda sem fala: por último
                {"id": 14, "channel": "L", "cluster": "L", "merged_into": None}]               # o canal do usuário fica fora
    names = number_voices(clusters, {10: 9_000, 11: 2_000, 12: 1_000})
    assert names.by_cluster == {11: 2, 10: 1, 12: 1, 13: 3}                      # a mescla leva o grupo para a primeira fala dele
    assert names.title(13, "Voz SPEAKER_02") == "Voz 3" and names.title(14, "Voz L") == "Voz L"


def test_transcript_timeline_and_review_show_voz_n_like_the_reading_and_the_export(qtbot, platform, store):
    from listener.rag import export
    from listener.ui.meeting import MeetingWindow
    from listener.ui.review import ReviewDialog
    from listener.ui.review_cards import BADGE_ROLE, TITLE_ROLE

    mid, items = _processed(platform, store)
    review.new_person(store, items[0].cluster_id, "Ana", role="CFO")              # a primeira voz ganha nome; a segunda segue sem
    raw = [r.name for r in review.transcript_rows(store, mid)]
    assert "Voz 2" in raw and not any("SPK" in n for n in raw)                      # F5.5: a leitura já dá o nome da tela
    assert "Voz 2" in export.transcript_markdown(export.gather(store, mid))
    win = MeetingWindow(store, mid)
    qtbot.addWidget(win)
    shown = [r.name for r in win.transcript.model.rows]
    assert "Voz 2" in shown and not any("SPK" in n for n in shown)                  # o número segue a voz: confirmar não renumera
    assert "Voz 2" in [lane.label for lane in win.timeline.view.lanes]
    first = win.transcript.model.index(shown.index("Voz 2"))
    assert win.transcript.model.data(first).split("] ", 1)[1].startswith("Voz 2: ")  # o texto do leitor de tela, o mesmo nome
    dlg = ReviewDialog(store, mid)
    qtbot.addWidget(dlg)
    titles = [dlg.list.item(i).data(TITLE_ROLE) for i in range(dlg.list.count())]
    assert titles == ["Voz 1", "Voz 2"] and dlg.list.item(0).data(BADGE_ROLE) == "confirmada"
    assert dlg.list.item(1).text().startswith("Voz 2 · ") and dlg.list.item(1).data(BADGE_ROLE) == "sem nome"


# ---------------------------------------------------------------- U-081: a fala e a legenda
def test_speech_rows_carry_time_speaker_and_the_lane_color_and_grow_with_the_text(qtbot, store):
    from PySide6.QtWidgets import QStyleOptionViewItem

    from listener.ui.meeting import MeetingWindow
    from listener.ui.speech_delegate import COLOR_ROLE, ROLE_ROLE, SPEAKER_ROLE, STAMP_ROLE, TEXT_ROLE, SpeechDelegate

    who = _people(store)
    mid, _job = _meeting(store, who)
    win = MeetingWindow(store, mid)
    qtbot.addWidget(win)
    model, lanes = win.transcript.model, win.timeline.view.lanes
    i = model.index(1)
    row = model.rows[1]
    assert (model.data(i, STAMP_ROLE), model.data(i, SPEAKER_ROLE), model.data(i, TEXT_ROLE)) == (row.t_start_ms, "Bia", row.shown_text)
    assert model.data(model.index(0), ROLE_ROLE) == "CFO"
    lane_of = {lane.label: n for n, lane in enumerate(lanes)}
    assert model.data(i, COLOR_ROLE) == lane_of["Bia"] and model.data(model.index(0), COLOR_ROLE) == lane_of["Ana"]
    delegate = win.transcript.view.itemDelegate()
    assert isinstance(delegate, SpeechDelegate)
    opt = QStyleOptionViewItem()
    opt.rect.setWidth(500)
    opt.font = win.transcript.view.font()
    short = delegate.sizeHint(opt, i).height()
    model.rows[1] = row.__class__(**{**{f: getattr(row, f) for f in row.__slots__}, "text": "longa " * 80, "text_llm": None})
    assert delegate.sizeHint(opt, i).height() > short + 30                         # o texto longo quebra e a fala cresce
    win.resize(900, 600)
    win.show()
    win.show_tab("transcricao")
    win.transcript.view.viewport().grab()                                           # a pintura (tempo, ponto, nome) não falha


def test_live_captions_use_the_speech_delegate_with_a_color_per_speaker_and_the_flag(qtbot, store):
    from listener.store.captions import CaptionInsert
    from listener.ui.live import LiveWindow, caption_key
    from listener.ui.speech_delegate import COLOR_ROLE, FLAG_ROLE, ROLE_ROLE, SPEAKER_ROLE, SpeechDelegate

    mid = store.create_meeting("Diretoria", None, None, None)
    for t0, text, label in ((1000, "bom dia", "Ana — CFO"), (4000, "oi", None), (7000, "seguimos", "Bia"), (9000, "ok", "Ana — CFO")):
        store.captions.insert(CaptionInsert(mid, "R", t0, t0 + 2000, text, None, label, 0.8, "fake:small", 6000))
    marked: set[Any] = set()
    win = LiveWindow(store, mid, on_mark_caption=lambda row: (True, "ok"), marked_captions=marked)
    qtbot.addWidget(win)
    assert isinstance(win.list.itemDelegate(), SpeechDelegate)
    items = [win.list.item(i) for i in range(win.list.count())]
    assert [it.data(SPEAKER_ROLE) for it in items] == ["Ana", "", "Bia", "Ana"] and items[0].data(ROLE_ROLE) == "CFO"
    assert [it.data(COLOR_ROLE) for it in items] == [0, None, 1, 0]                  # a cor na ordem em que o falante apareceu
    marked.add(caption_key(win.captions[0]))
    win._paint(items[0], win.captions[0])
    assert items[0].data(FLAG_ROLE) is True and items[1].data(FLAG_ROLE) is False


# ---------------------------------------------------------------- U-082 e U-034: a linha do tempo
def test_timeline_cursor_in_accent_marker_in_text_and_the_footer_teaches_the_click(qtbot, store):
    from listener.ui.meeting import MeetingWindow
    from listener.ui.timeline_view import CLICK_TEXT, CURSOR_TONE, EMPTY_TEXT, MARKER_TONE, TimelineTab

    assert (CURSOR_TONE, MARKER_TONE) == (Tone.ACCENT, Tone.TEXT)
    who = _people(store)
    mid, _job = _meeting(store, who)
    store.add_marker(mid, 100_000, "auditoria")
    win = MeetingWindow(store, mid)
    qtbot.addWidget(win)
    win.resize(900, 600)
    win.show()
    win.show_tab("linha")
    view = win.timeline.view
    view.set_cursor(150_000)
    image = view.viewport().grab().toImage()
    dpr = image.devicePixelRatio()
    x = round(view.x_of(150_000) * dpr)
    assert near(image.pixelColor(x, round(60 * dpr)), LIGHT.accent)                  # o cursor no destaque, não em vermelho
    empty = store.create_meeting("Sem falas", None, None, None)
    store.finish_meeting(empty, 60_000)
    bare = TimelineTab(store, empty, rows=list)
    qtbot.addWidget(bare)
    assert bare.note.text() == CLICK_TEXT and bare.note.text() != EMPTY_TEXT     # U-034: o aviso fica no meio da vista
    silent = store.create_meeting("Sem áudio", None, None, None)
    nothing = TimelineTab(store, silent, rows=list)
    qtbot.addWidget(nothing)
    assert nothing.note.text() == ""                                                 # sem áudio nem falas, nada a ensinar


# ---------------------------------------------------------------- A-15, U-079, U-059: a ata lida, editada e vazia
def test_minutes_read_formatted_edit_the_markdown_and_editing_means_open_or_modified(qtbot, store):
    from listener.ui.minutes_view import TextTab

    who = _people(store)
    mid, job = _meeting(store, who)
    _minutes(store, mid, job)
    asked: list[JobStep] = []
    tab = TextTab(store, mid, SummaryKind.MINUTES, asked.append)
    qtbot.addWidget(tab)
    tab.reload(set(), {}, False)
    assert tab.stack.currentWidget() is tab.reader and not tab.editing()
    shown = tab.reader.toPlainText()
    assert "Decisões" in shown and "##" not in shown and "**" not in shown            # formatada, não o Markdown cru
    assert tab.editor.toPlainText() == MINUTES_MD and re.search(r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}", tab.info.text())   # U-079
    tab.btn_edit.click()
    assert tab.stack.currentWidget() is tab.editor and tab.editing() and tab.btn_save.isVisibleTo(tab)
    assert tab.btn_save.property(KIND_PROP) == PRIMARY
    _minutes(store, mid, job, "# Nova versão")                                       # chega outra versão com o Markdown aberto
    tab.reload(set(), {}, False)
    assert tab.newer and tab.editor.toPlainText() == MINUTES_MD and not tab.notice_bar.isHidden()   # R3-01: aberto já é edição
    tab.cancel_edit()                                                                # cancelar descarta e mostra a nova
    assert not tab.editing() and tab.stack.currentWidget() is tab.reader and "Nova versão" in tab.reader.toPlainText()
    tab.start_edit()
    tab.editor.setPlainText("# Minha ata\n\ntexto meu")
    assert tab.save() and tab.stack.currentWidget() is tab.reader and not tab.editing()
    assert "Minha ata" in tab.reader.toPlainText() and "editado por você" in tab.info.text()


def test_without_minutes_the_empty_state_offers_to_generate_and_waiting_moves_the_action_to_the_bar(qtbot, store):
    from listener.ui.components import EmptyState
    from listener.ui.minutes_view import TextTab

    who = _people(store)
    mid, _job = _meeting(store, who)
    asked: list[JobStep] = []
    tab = TextTab(store, mid, SummaryKind.MINUTES, asked.append)
    qtbot.addWidget(tab)
    tab.reload(set(), {}, False)
    assert tab._empty is None                                                        # escondida, o vazio ainda não nasceu
    tab.show()
    assert isinstance(tab.stack.currentWidget(), EmptyState) and tab.empty.label.text() == "Ata ainda não gerada."
    assert tab.empty.action.text() == "Gerar ata" and tab.empty.action.isVisibleTo(tab) and not tab.btn_edit.isVisibleTo(tab)
    assert not tab.info.isVisibleTo(tab)                                             # o vazio diz tudo: sem a origem nem o Editar
    tab.empty.action.click()
    assert asked == [JobStep.MINUTES]
    tab.reload(set(), {}, True)                                                      # LLM em andamento: a ação apagada
    assert not tab.empty.action.isEnabled()
    tab.set_extra("A ata espera a identificação de 2 voz(es).", "Gerar ata mesmo assim", lambda: None)
    tab.reload(set(), {}, False)
    assert not tab.empty.action.isVisibleTo(tab) and tab.btn_extra.isVisibleTo(tab) and not tab.notice_bar.isHidden()
    assert tab.notice_bar.kind == "info"                                             # a espera informa; não é aviso


def test_empty_markers_actions_and_transcript_show_the_empty_state(qtbot, store):
    from listener.ui.meeting import MeetingWindow

    mid = store.create_meeting("Vazia", None, None, None)
    store.finish_meeting(mid, 30_000)
    _pipeline_job(store, mid)
    win = MeetingWindow(store, mid)
    qtbot.addWidget(win)
    win.show()
    for key, tab in (("marcadores", win.markers), ("acoes", win.llm.actions_tab), ("transcricao", win.transcript)):
        assert tab._empty is None                                                    # a aba escondida não cria o vazio
        win.show_tab(key)
        assert tab.stack.currentWidget() is tab.empty
    store.add_marker(mid, 5_000, "ponto")
    win.markers.reload()
    assert win.markers.stack.currentWidget() is win.markers.list and win.markers.list.item(0).icon().cacheKey()


# ---------------------------------------------------------------- U-080: as ações
def test_actions_stretch_wrap_and_show_the_overdue_due_date_in_a_red_badge(qtbot, store):
    from datetime import date

    from PySide6.QtWidgets import QComboBox, QHeaderView

    from listener.ui.actions_table import ActionsTab
    from listener.ui.components import BadgeDelegate

    who = _people(store)
    mid, job = _meeting(store, who)
    sid = _minutes(store, mid, job)
    store.actions.insert_many([ActionInsert(mid, sid, "Mandar a planilha revisada " * 6, None, who["bia"], "2026-10-01", 1000, None, job),
                               ActionInsert(mid, sid, "Fechar a proposta", None, who["ana"], "2026-12-31", None, None, job),
                               ActionInsert(mid, sid, "Feita", None, who["ana"], "2026-01-01", None, None, job, ActionStatus.DONE)])
    tab = ActionsTab(store, mid, today=lambda: date(2026, 10, 28))
    qtbot.addWidget(tab)
    tab.reload()
    t = tab.table
    assert t.horizontalHeader().sectionResizeMode(0) == QHeaderView.ResizeMode.Stretch and t.wordWrap()
    assert isinstance(t.itemDelegateForColumn(2), BadgeDelegate)
    row = {t.item(r, 0).text().split()[0]: r for r in range(3)}
    late, ahead, done = row["Mandar"], row["Fechar"], row["Feita"]
    assert (t.item(late, 2).text(), t.item(ahead, 2).text(), t.item(done, 2).text()) == ("01/10/2026 (atrasada)", "31/12/2026",
                                                                                        "01/01/2026")    # dd/mm/aaaa
    assert t.item(late, 2).data(TONE_ROLE) == Tone.DANGER.value and t.item(ahead, 2).data(TONE_ROLE) is None
    assert t.item(done, 2).data(TONE_ROLE) is None and t.item(late, 0).data(TONE_ROLE) is None   # feita não atrasa; o selo só no prazo
    combo = t.cellWidget(late, 3)
    assert isinstance(combo, QComboBox)
    combo.setCurrentIndex(combo.findData(ActionStatus.DONE))                         # marcada feita: o selo sai na hora
    assert t.item(late, 2).text() == "01/10/2026" and t.item(late, 2).data(TONE_ROLE) is None


# ---------------------------------------------------------------- U-078 e U-083: o cabeçalho e o player
def test_title_has_no_frame_until_focus_facts_are_captions_and_menus_carry_their_arrow(qtbot, store):
    from PySide6.QtWidgets import QLineEdit, QPushButton, QVBoxLayout, QWidget

    from listener.ui.meeting import MeetingWindow
    from listener.ui.style import FLAT_PROP

    who = _people(store)
    mid, _job = _meeting(store, who)
    win = MeetingWindow(store, mid)
    qtbot.addWidget(win)
    title = win.llm.title_tab.title
    assert title.property(FLAT_PROP) and title.font().pixelSize() == 20 and title.property("theme_font") == "subtitle"
    assert win.info.property("theme_font") == "caption" and win.language_bar.label.property("theme_font") == "caption"
    assert isinstance(win.btn_export, QPushButton) and win.btn_export.menu() is not None
    assert isinstance(win.llm.title_tab.btn_menu, QPushButton) and win.llm.title_tab.btn_menu.menu() is not None
    box = QWidget()                                                                   # sem foco, o campo plano não pinta o fundo
    lay = QVBoxLayout(box)
    flat, normal = QLineEdit("título"), QLineEdit("campo")
    flat.setProperty(FLAT_PROP, True)
    for w in (flat, normal):
        lay.addWidget(w)
    qtbot.addWidget(box)
    box.show()
    def bg(w: QLineEdit) -> QColor:
        image = w.grab().toImage()
        return image.pixelColor(image.width() - 4, image.height() // 2)
    assert near(bg(flat), LIGHT.window, 6) and not near(bg(normal), LIGHT.window, 6)


def test_player_bar_icons_follow_the_state_and_the_time_has_a_fixed_width(qtbot, store):
    from PySide6.QtGui import QFont

    from listener.ui.player import PLAYING, Player, PlayerState
    from listener.ui.player_bar import PlayerBar

    mid = store.create_meeting("Sintética", None, None, None)
    player = Player(store, FakeOutputs())
    try:
        bar = PlayerBar(player, mid)
        qtbot.addWidget(bar)
        assert [str(b.property(icons.ICON_PROP)).split(":")[0] for b in (bar.btn_play, bar.btn_back, bar.btn_fwd)] == \
            ["play", "back-10", "forward-10"]
        assert bar.btn_play.text() == "Tocar" and bar.time.minimumWidth() == bar.time.maximumWidth() > 0
        assert bar.time.font().featureValue(QFont.Tag.fromString("tnum")) == 1          # algarismos de largura igual
        width = bar.time.width()
        bar.show_state(PlayerState(PLAYING, mid, 3_725_000, 5_000_000, title="Sintética"))
        assert bar.btn_play.text() == "Pausar" and str(bar.btn_play.property(icons.ICON_PROP)).startswith("pause:")
        assert bar.time.text() == "62:05 / 83:20" and bar.time.width() == width
    finally:
        player.shutdown()


# ---------------------------------------------------------------- U-084, U-085, U-080: o Histórico
def test_history_search_icon_index_on_the_tab_row_status_badge_and_cost_on_the_right(qtbot, store):
    from listener.ui.components import BadgeDelegate
    from listener.ui.history import HistoryPage
    from listener.ui.history_data import COST_COL, STATUS_COL, MeetingLine, MeetingsModel

    page = HistoryPage(store)
    qtbot.addWidget(page)
    try:
        assert page.search_icon.icon().cacheKey() and page.search.actions() == [page.search_icon]
        assert page.tabs.cornerWidget(Qt.Corner.TopRightCorner) is page.coverage
        assert isinstance(page.table.itemDelegateForColumn(STATUS_COL), BadgeDelegate) and not page.table.verticalHeader().isVisible()
        assert page.badge.isHidden()                                                 # sem busca, sem selo
        model = MeetingsModel()
        model.replace([MeetingLine(1, "2026-10-28T10:00:00-03:00", "Comitê", 60_000, ("Ana",), "processada", 0.32),
                       MeetingLine(2, "2026-10-27T10:00:00-03:00", "Diretoria", 60_000, (), "revisada", None)])
        assert model.data(model.index(0, STATUS_COL), TONE_ROLE) == Tone.WARNING.value
        assert model.data(model.index(1, STATUS_COL), TONE_ROLE) == Tone.SUCCESS.value
        assert model.data(model.index(0, STATUS_COL)) == "falantes a revisar"          # o texto fica para o leitor de tela
        right = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        assert model.data(model.index(0, COST_COL), Qt.ItemDataRole.TextAlignmentRole) == right
        assert model.headerData(COST_COL, Qt.Orientation.Horizontal, Qt.ItemDataRole.TextAlignmentRole) == right
        page._set_badge("busca híbrida", Tone.SUCCESS, "3 trecho(s)")
        assert not page.badge.isHidden() and page.badge.badge_tone == Tone.SUCCESS and page.badge_count.text() == "3 trecho(s)"
    finally:
        page.shutdown()


def test_history_html_has_no_fixed_color_and_the_classes_follow_both_themes(qtbot, store):
    from listener.llm.fake import FakeProvider
    from listener.rag import chat_global, index, search
    from listener.ui.history import CHAT_EMPTY, results_html
    from listener.ui.live_text import partial_html

    who = _people(store)
    mid, _job = _meeting(store, who)
    index.sync_text(store, mid)
    index.fill_vectors(store, mid, provider=FakeProvider())
    result = search.search(store, "orçamento")
    thread = store.chat.create_thread("Orçamento?")
    store.chat.add(None, "user", "Qual o orçamento?", thread_id=thread)
    store.chat.add(None, "assistant", "Discutiram o orçamento de 2027.", "gemini", "m", [], thread_id=thread)
    pages = (results_html(result), chat_global.thread_html(store, thread), CHAT_EMPTY,
             partial_html({"summary": "x", "decisions": [{"text": "Cortar"}]}, {("decisions", "cortar")}, True))
    assert result.hits and all(not HEX.search(p) for p in pages) and "class='note'" in pages[0]
    for t in (LIGHT, DARK):                                                          # as classes nos tokens de cada tema
        css = html_css(t)
        assert f".note {{ color: {t.secondary}; }}" in css and f"a {{ color: {t.accent}; }}" in css


def test_history_open_actions_wrap_and_badge_only_the_due_date(qtbot, store):
    from listener.ui.history import HistoryPage

    who = _people(store)
    mid, job = _meeting(store, who)
    sid = _minutes(store, mid, job)
    store.actions.insert_many([ActionInsert(mid, sid, "Mandar a planilha", None, who["bia"], "2020-01-10", None, None, job)])
    page = HistoryPage(store)
    qtbot.addWidget(page)
    try:
        page.reload_actions()
        t = page.action_table
        assert t.wordWrap() and t.item(0, 2).data(TONE_ROLE) == Tone.DANGER.value and t.item(0, 0).data(TONE_ROLE) is None
    finally:
        page.shutdown()


# ---------------------------------------------------------------- U-086 e U-087: a revisão e a ficha
def test_review_cards_key_badges_and_the_primary_follows_the_stage(qtbot, platform, store):
    from listener.ui.review import ReviewDialog
    from listener.ui.review_cards import BADGE_TONE_ROLE, KeyButton, VoiceCardDelegate

    mid, items = _processed(platform, store)
    dlg = ReviewDialog(store, mid)
    qtbot.addWidget(dlg)
    dlg.show()
    assert isinstance(dlg.list.itemDelegate(), VoiceCardDelegate)
    assert all(dlg.list.item(i).data(BADGE_TONE_ROLE) == Tone.WARNING.value for i in range(dlg.list.count()))
    keys = {b.text(): b.key for b in dlg.findChildren(KeyButton)}
    assert keys == {"Confirmar pyannote": "Enter", "Aceitar LLM": "L", "Outra pessoa": "O", "Nova pessoa": "N", "É a mesma que…": "M",
                    "Ignorar": "I", "Ouvir 5 s": "Espaço", "Editar pessoa": "E"}
    assert dlg.btn_confirm.width() >= dlg.btn_confirm.full_width > dlg.fontMetrics().horizontalAdvance("Confirmar pyannote") + 40
    assert dlg.btn_confirm.property(KIND_PROP) == PRIMARY and dlg.btn_finish.property(KIND_PROP) is None
    assert not any(b.isDefault() for b in (dlg.btn_confirm, dlg.btn_finish))         # o Enter segue da lista (U-006)
    dlg.list.viewport().grab()                                                       # os cartões pintam
    for it in items:
        review.new_person(store, it.cluster_id, f"Pessoa {it.cluster_id}")
    dlg.reload()
    assert dlg.btn_finish.property(KIND_PROP) == PRIMARY and dlg.btn_confirm.property(KIND_PROP) is None
    assert {dlg.list.item(i).data(BADGE_TONE_ROLE) for i in range(dlg.list.count())} == {Tone.SUCCESS.value}
    assert store.segments.get_cluster(items[0].cluster_id)["status"] == ClusterStatus.CONFIRMED


def test_person_sheet_puts_delete_in_red_on_the_left_far_from_save(qtbot):
    from listener.ui.person import PersonData, PersonDialog
    from listener.ui.theme import DANGER

    dlg = PersonDialog(data=PersonData("Ana", "CFO"), can_delete=True)
    qtbot.addWidget(dlg)
    dlg.show()
    assert dlg.btn_delete is not None and dlg.btn_delete.property(KIND_PROP) == DANGER and not dlg.btn_delete.isDefault()
    assert dlg.btn_save.property(KIND_PROP) == PRIMARY and dlg.btn_save.isDefault()
    gap = dlg.btn_save.mapTo(dlg, QPoint(0, 0)).x() - dlg.btn_delete.mapTo(dlg, QPoint(dlg.btn_delete.width(), 0)).x()
    assert gap >= 48                                                                 # à esquerda, longe do Salvar


# ---------------------------------------------------------------- U-088: o assistente
def test_wizard_is_modern_with_the_step_and_a_20_px_title(qtbot, tmp_path):
    from PySide6.QtWidgets import QLabel, QWizard, QWizardPage

    from listener.ui.wizard import FirstRunWizard
    from tests.fake_platform import FakePlatform

    wiz = FirstRunWizard(FakePlatform(tmp_path), tmp_path / "db")
    qtbot.addWidget(wiz)
    assert wiz.wizardStyle() == QWizard.WizardStyle.ModernStyle
    pages = [wiz.page(i) for i in wiz.pageIds()]
    assert [p.subTitle() for p in pages] == [f"Passo {n} de 6" for n in range(1, 7)]
    intro = [lab.text() for lab in wiz.user_page.findChildren(QLabel)]
    assert any(t.startswith("O canal do microfone é sempre você") for t in intro)    # o texto da página desceu para ela
    assert wiz.button(QWizard.WizardButton.NextButton).property(KIND_PROP) == PRIMARY
    wiz.show()
    wiz.next()
    head = [lab for lab in wiz.findChildren(QLabel) if not isinstance(lab.parentWidget(), QWizardPage)]
    title = next(lab for lab in head if lab.text() == "Seus dados")
    step = next(lab for lab in head if lab.text() == "Passo 2 de 6")
    assert title.font().pixelSize() == 20 and step.property("theme_font") == "caption"


# ---------------------------------------------------------------- A-17: reunião e Histórico dentro da área livre
def test_meeting_and_history_open_inside_the_free_area(qtbot, store, monkeypatch):
    from listener.ui import history as history_ui
    from listener.ui import meeting as meeting_ui

    fitted: list[str] = []
    monkeypatch.setattr(meeting_ui, "fit_to_screen", lambda w: fitted.append(type(w).__name__))
    monkeypatch.setattr(history_ui, "fit_to_screen", lambda w: fitted.append(type(w).__name__))
    mid = store.create_meeting("Comitê", None, None, None)
    win = meeting_ui.MeetingWindow(store, mid)
    hist = history_ui.HistoryWindow(store)
    for w in (win, hist):
        qtbot.addWidget(w)
        w.show()
    try:
        assert fitted == ["MeetingWindow", "HistoryWindow"]
    finally:
        hist.page.shutdown()


def test_the_speech_delegate_reads_its_roles_from_any_model(qtbot):
    """O delegate não conhece a transcrição: lê os papéis (um item sem falante não tem ponto nem nome)."""
    from PySide6.QtGui import QStandardItem, QStandardItemModel
    from PySide6.QtWidgets import QListView

    from listener.ui.speech_delegate import STAMP_ROLE, TEXT_ROLE, SpeechDelegate

    model = QStandardItemModel()
    item = QStandardItem("[00:05] texto")
    item.setData(5000, STAMP_ROLE)
    item.setData("texto solto", TEXT_ROLE)
    model.appendRow(item)
    view = QListView()
    view.setModel(model)
    view.setItemDelegate(SpeechDelegate(view, flags=True))
    qtbot.addWidget(view)
    view.show()
    assert view.sizeHintForRow(0) >= 20 and isinstance(model.index(0, 0), QModelIndex)
    view.viewport().grab()
