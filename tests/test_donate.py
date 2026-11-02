"""F5.12 L2 (U-123 a U-127): o apoio sem as janelas. Os textos do convite com os números reais do banco (Interface,
Comportamento 2), a cadência pelo estado (Comportamento 1 e 4, Aceite 2), o `Support` com relógio falso (mostrado, adiado,
apoio informado), o agendamento só no aviso de reunião processada ou de ata pronta, a área de transferência, o navegador
por monkeypatch com o endereço de `about.py` e o log sem o e-mail (Aceite 3, D-053)."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from listener import about
from listener.model import ActionStatus, MeetingStatus, SummaryKind
from listener.ui import donate
from listener.ui.donate import Stats, cadence_block, duration_text, invite_texts, minutes_done, parse_when
from tests.log_privacy import leaks

ROOT = Path(__file__).resolve().parent.parent
DONATE_MODULES = tuple(ROOT / "listener/ui" / f for f in ("donate.py", "donate_dialogs.py", "donate_card.py", "donate_invite.py",
                                                         "about_dialog.py", "settings_about.py"))
SPEC_MESSAGE = ("São 9 h de conversa que você pode buscar, ouvir e citar quando quiser, e 47 ações que não se perderam. O "
                "listener-meet é gratuito, de código aberto e feito por uma pessoa só, com muito carinho. Se ele está te ajudando, "
                "que tal retribuir com um gesto simbólico? R$ 0,99 pelo Pix ou US$ 0,20 pelo PayPal mantêm o projeto vivo e "
                "crescendo. Cada apoio conta muito!")
BRT = timezone(timedelta(hours=-3))


class Clock:
    """O relógio falso do apoio (datas com fuso)."""

    def __init__(self) -> None:
        self.now = datetime(2026, 11, 2, 10, 0, tzinfo=BRT)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw: float) -> None:
        self.now += timedelta(**kw)


def processed(store: Any, n: int, ms: int = 3_600_000, status: MeetingStatus = MeetingStatus.PROCESSED) -> list[int]:
    """`n` reuniões processadas de `ms` cada (a 3ª libera o primeiro convite)."""
    out = []
    for i in range(n):
        mid = store.create_meeting(f"Comitê {i}", None, None, None)
        store.finish_meeting(mid, ms, status)
        out.append(mid)
    return out


# ---------------------------------------------------------------- textos (Interface, Comportamento 2)
def _plain(text: str) -> str:
    """O texto sem o espaço que não quebra (entre o "R$" e o valor, o número e a unidade): o mesmo da spec."""
    return text.replace(donate.NBSP, " ")


def test_the_invite_says_what_the_app_already_kept_with_the_spec_message():
    title, message = invite_texts(Stats(12, int(9.2 * 3_600_000), 47))
    assert title == "O listener-meet já guardou 12 reuniões suas"
    assert _plain(message) == SPEC_MESSAGE
    assert donate.NBSP + "0,99" in message and "9" + donate.NBSP + "h" in message      # a quebra não separa o valor
    assert invite_texts(None) == ("Que bom ter você usando o listener-meet", donate.INVITE_PITCH)    # sem número ainda
    assert message.endswith(donate.INVITE_PITCH)
    _title, one = invite_texts(Stats(3, 40 * 60_000, 1))
    assert _plain(one).startswith("São 40 min de conversa que você pode buscar, ouvir e citar quando quiser, e 1 ação que não se "
                                  "perdeu.")
    _title, none = invite_texts(Stats(3, 3 * 3_600_000, 0))
    assert _plain(none).startswith("São 3 h de conversa que você pode buscar, ouvir e citar quando quiser. O listener-meet")
    assert [_plain(duration_text(ms)) for ms in (0, 59_000, 3_000_000, 3_600_000, 5_400_000, 33_840_000)] == \
        ["1 min", "1 min", "50 min", "1 h", "2 h", "9 h"]


def test_the_support_text_and_the_thanks_are_the_spec_ones():
    assert _plain(donate.SUPPORT_TEXT) == ("O listener-meet é gratuito e sempre vai ser. Se ele te poupa tempo, um R$ 0,99 pelo Pix "
                                           "ou um US$ 0,20 pelo PayPal ajuda a manter tudo funcionando e a trazer novidades.")
    assert donate.THANKS == "Obrigado de verdade! É o seu apoio que faz o listener-meet continuar."


def test_the_numbers_come_from_processed_meetings_and_the_current_minutes(store):
    from listener.store.actions import ActionInsert
    from listener.store.summaries import SummaryInsert

    mids = processed(store, 2, 3_600_000) + processed(store, 1, 1_800_000, MeetingStatus.REVIEWED)
    processed(store, 1, 9_000_000, MeetingStatus.RECORDED)                    # gravada, ainda sem processar: fora
    old = store.summaries.insert(SummaryInsert(mids[0], SummaryKind.MINUTES, "{}", "ata", "fake", "m", "1", None, None, "pt-BR"))
    store.actions.insert_many([ActionInsert(mids[0], old, "antiga", None, None, None, None, None, None)])
    new = store.summaries.insert(SummaryInsert(mids[0], SummaryKind.MINUTES, "{}", "ata", "fake", "m", "1", None, None, "pt-BR"))
    rows = [ActionInsert(mids[0], new, t, None, None, None, None, None, None, s) for t, s in
            (("a", ActionStatus.OPEN), ("b", ActionStatus.OPEN), ("c", ActionStatus.DONE), ("d", ActionStatus.DISCARDED))]
    store.actions.insert_many(rows)                                           # a ata nova supera a antiga e as ações dela
    assert donate.meeting_stats(store) == Stats(3, 9_000_000, 3)


# ---------------------------------------------------------------- cadência (Comportamento 1 e 4)
def test_the_cadence_needs_the_switch_three_processed_meetings_and_the_date():
    now = datetime(2026, 11, 2, 10, 0, tzinfo=BRT)
    assert cadence_block(True, 3, None, now) == ""                           # o primeiro, depois da 3ª processada
    assert "2 reuniões processadas" in cadence_block(True, 2, None, now)
    assert cadence_block(False, 30, None, now) == "desligado na página Sobre"                       # nunca desligado
    assert cadence_block(True, 3, now + timedelta(seconds=1), now).startswith("espera até")
    assert cadence_block(True, 3, now, now) == ""
    assert parse_when("") is None and parse_when("lixo") is None
    assert parse_when("2026-12-02T10:00:00-03:00") == datetime(2026, 12, 2, 10, 0, tzinfo=BRT)
    assert parse_when("2026-12-02T10:00:00").tzinfo is not None                # sem fuso: o local


def test_the_support_state_after_shown_later_no_answer_and_donated(store):
    clock, notices = Clock(), list[str]()
    support = donate.Support(store, notices.append, clock=clock)
    assert support.enabled() and support.next_at() is None and support.shown() == 0
    assert support.mark_shown() == 1 and support.next_at() == clock.now + timedelta(days=30)       # no máximo um a cada 30 dias
    support.defer(donate.LATER_DAYS, "Agora não")
    assert support.next_at() == clock.now + timedelta(days=30)
    clock.advance(days=3)
    support.defer(donate.EXPIRED_DAYS, "sem resposta")
    assert support.next_at() == clock.now + timedelta(days=7)
    support.donated("Pix, teste")
    assert support.next_at() == clock.now + timedelta(days=180) and parse_when(store.get_setting(donate.DONATED_AT)) == clock.now
    assert notices == [donate.THANKS] and support.shown() == 1
    donate.Support(store, clock=clock).donated("sem aviso")                  # sem quem avise (a página sem o controlador)


def test_only_the_processed_and_the_minutes_ready_notices_schedule_the_invite(qapp, store):
    support = donate.Support(store)
    seen: list[bool] = []
    support.meeting_notified.connect(lambda: seen.append(True))
    minutes = {"kind": "llm", "timings_json": json.dumps({"llm": {"done": ["identify", "minutes"]}})}
    core = {"kind": "llm", "timings_json": json.dumps({"llm": {"done": ["identify", "title"]}})}
    for job, expected in (({"kind": "meeting"}, True), ({"kind": "reprocess"}, True), (minutes, True), (core, False),
                          ({"kind": "llm", "timings_json": "não é json"}, False), ({"kind": "index"}, False)):
        assert support.job_noticed(job) is expected, job
    assert seen == [True, True, True]
    assert minutes_done(minutes) and not minutes_done({"timings_json": None}) and not minutes_done({"timings_json": "[]"})


# ---------------------------------------------------------------- área de transferência, navegador e log (Aceite 3)
def test_copy_key_puts_the_email_on_the_clipboard_and_the_log_never_has_it(qapp, store, caplog):
    from PySide6.QtGui import QGuiApplication

    support = donate.Support(store)
    with caplog.at_level(logging.DEBUG, logger="listener"):
        assert support.copy_email(donate.PIX) == about.DONATION_EMAIL
        assert QGuiApplication.clipboard().text() == about.DONATION_EMAIL
        QGuiApplication.clipboard().setText("")
        support.copy_email(donate.PAYPAL)
    assert QGuiApplication.clipboard().text() == about.DONATION_EMAIL
    assert "a chave do Pix copiada" in caplog.text and "o e-mail do PayPal copiado" in caplog.text
    assert about.DONATION_EMAIL not in caplog.text and "herculanocm" not in caplog.text


def test_open_paypal_calls_the_browser_with_the_about_address_and_copies_the_email(qapp, store, monkeypatch, caplog):
    from PySide6.QtGui import QDesktopServices, QGuiApplication

    opened: list[str] = []

    def fake_open(url: Any) -> bool:
        opened.append(url.toString())
        return len(opened) == 1                                               # a segunda vez o navegador "não abre"

    monkeypatch.setattr(QDesktopServices, "openUrl", fake_open)
    QGuiApplication.clipboard().setText("")
    support = donate.Support(store)
    with caplog.at_level(logging.INFO, logger="listener"):
        assert support.open_paypal() is True
        assert support.open_paypal() is False
    assert opened == [about.PAYPAL_URL, about.PAYPAL_URL] and QGuiApplication.clipboard().text() == about.DONATION_EMAIL
    assert "o PayPal abriu no navegador" in caplog.text and "o navegador não abriu o PayPal" in caplog.text
    assert about.DONATION_EMAIL not in caplog.text


def test_no_log_call_of_the_new_modules_takes_the_email():
    """D-053 (Comportamento 5): por AST, nenhum `log.*` dos módulos do apoio leva o e-mail (o nome dele nem um `email`)."""
    forbidden = frozenset({"DONATION_EMAIL", "email", "emails", "key", "path", "paths"})
    found = {p.name: leaks(p, forbidden) for p in DONATE_MODULES}
    assert {name: f for name, f in found.items() if f} == {}


@pytest.mark.parametrize("raw", ["", "2026-01-01T00:00:00+00:00"])
def test_the_switch_and_the_internal_keys_are_in_the_specs(raw):
    from listener.config.defaults import SPECS

    assert SPECS[donate.ENABLED].default is True and not SPECS[donate.ENABLED].internal
    assert SPECS[donate.ENABLED].label == "Mostrar o convite para apoiar de vez em quando"
    assert all(SPECS[k].internal for k in (donate.NEXT_AT, donate.SHOWN, donate.DONATED_AT))
    assert SPECS[donate.NEXT_AT].error(raw) is None and SPECS[donate.SHOWN].error("-1") is not None
