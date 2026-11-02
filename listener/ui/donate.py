"""F5.12 L2 (U-123 a U-127): o apoio ao projeto, sem as janelas (`ui/donate_dialogs.py`) e sem o cartão do convite
(`ui/donate_invite.py`). O app não faz nenhuma chamada de rede para doação e não confere pagamento (Comportamento 3).

- Estado em `settings` (CP9; CP3, só cresce): `donate.enabled` (a página Sobre, CP8), `donate.next_at` (a data a partir da
  qual o convite pode voltar), `donate.shown` (quantas vezes ele apareceu) e `donate.donated_at`.
- Cadência (U-127, Comportamento 1 e 4; perguntas 2 e 3): o convite pode aparecer com `donate.enabled`, 3 ou mais reuniões
  processadas e `donate.next_at` vencido; mostrado, a data vai a +30 dias (no máximo um a cada 30 dias, também se o app sair
  com ele à vista); "Agora não" e o ✕ adiam 30 dias, sumir sem resposta em 2 min, 7, e "Já doei" ou "Pronto, doei", 180. O
  app parado (sem gravação, sem chamada, fora do assistente) o convite confere.
- Os números do título (Comportamento 2), em duas consultas curtas: as reuniões processadas (processada ou revisada), a
  duração delas e as ações da ata atual de cada uma (abertas e feitas).
- `Support`: o estado, a área de transferência ("Copiar chave", "Copiar e-mail"), o navegador ("Abrir o PayPal", pelo Qt:
  portátil, CP15), os diálogos e o agradecimento no aviso do app. `meeting_notified` sai no aviso de reunião processada ou
  de ata pronta (`job_noticed`, que o controlador chama) e agenda a checagem do convite.
- Log (Comportamento 5, D-053): "convite de apoio mostrado", "adiado" e "apoio informado", com a contagem; nunca o e-mail."""
from __future__ import annotations

import functools
import json
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices

from .. import about
from ..config.defaults import SPECS
from ..fmt import plural
from ..log import get_logger
from ..model import ActionStatus, JobKind, MeetingStatus, SummaryKind, SummaryStatus
from .common import copy_rich

if TYPE_CHECKING:
    from PySide6.QtWidgets import QDialog, QWidget

    from ..platform.base import Platform
    from ..store import Store

log = get_logger("ui.donate")

ENABLED, NEXT_AT, SHOWN, DONATED_AT = "donate.enabled", "donate.next_at", "donate.shown", "donate.donated_at"
MIN_PROCESSED = 3                    # U-127: o primeiro convite depois da 3ª reunião processada (pergunta 2)
SHOWN_DAYS, LATER_DAYS, EXPIRED_DAYS, DONATED_DAYS = 30, 30, 7, 180      # U-127 e Comportamento 4 (pergunta 3)
PIX, PAYPAL = "pix", "paypal"
METHOD_NAMES = {PIX: "Pix", PAYPAL: "PayPal"}
PROCESSING_KINDS = frozenset({str(JobKind.MEETING), str(JobKind.REPROCESS)})      # o aviso de reunião processada

NBSP = chr(0xA0)                     # nos textos que quebram a linha, o "R$" nunca fica longe do valor
PIX_MONEY, PAYPAL_MONEY = about.PIX_AMOUNT.replace(" ", NBSP), about.PAYPAL_AMOUNT.replace(" ", NBSP)
SUPPORT_HEAD = "Apoie o projeto"
SUPPORT_TEXT = (f"O {about.APP_NAME} é gratuito e sempre vai ser. Se ele te poupa tempo, um {PIX_MONEY} pelo Pix ou um "
                f"{PAYPAL_MONEY} pelo PayPal ajuda a manter tudo funcionando e a trazer novidades.")
THANKS = f"Obrigado de verdade! É o seu apoio que faz o {about.APP_NAME} continuar."
INVITE_FALLBACK_TITLE = f"Que bom ter você usando o {about.APP_NAME}"
INVITE_PITCH = (f"O {about.APP_NAME} é gratuito, de código aberto e feito por uma pessoa só, com muito carinho. Se ele está te "
                f"ajudando, que tal retribuir com um gesto simbólico? {PIX_MONEY} pelo Pix ou {PAYPAL_MONEY} pelo "
                "PayPal mantêm o projeto vivo e crescendo. Cada apoio conta muito!")


def local_now() -> datetime:
    return datetime.now().astimezone()


def _iso(when: datetime) -> str:
    return when.isoformat(timespec="seconds")


def parse_when(raw: str) -> datetime | None:
    """A data gravada (ISO com o fuso); vazia ou ilegível, None: sem espera (só um banco editado à mão chega a ilegível)."""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return None
    return when if when.tzinfo is not None else when.astimezone()


@dataclass(frozen=True, slots=True)
class Stats:
    """Os números do título do convite (Comportamento 2)."""
    meetings: int                    # reuniões processadas (processada ou revisada)
    duration_ms: int                 # a duração somada delas
    actions: int                     # as ações da ata atual de cada reunião, abertas e feitas


def meeting_stats(store: Store) -> Stats:
    """Duas consultas curtas, parametrizadas, na thread de quem chama (o convite, 8 s depois do aviso)."""
    meetings = store._q("SELECT COUNT(*), COALESCE(SUM(duration_ms), 0) FROM meetings WHERE status IN (?, ?)",
                        (str(MeetingStatus.PROCESSED), str(MeetingStatus.REVIEWED)))[0]
    actions = store._q("SELECT COUNT(*) FROM action_items a JOIN summaries s ON s.id = a.summary_id "
                       "WHERE s.status = ? AND s.kind = ? AND (a.status IS NULL OR a.status IN (?, ?))",
                       (str(SummaryStatus.CURRENT), str(SummaryKind.MINUTES), str(ActionStatus.OPEN), str(ActionStatus.DONE)))[0]
    return Stats(int(meetings[0]), int(meetings[1] or 0), int(actions[0]))


def duration_text(ms: int) -> str:
    """"9 h" (arredondado); abaixo de 1 h, os minutos ("40 min", ao menos 1). O número e a unidade não se separam na quebra."""
    minutes = max(1, int(ms / 60_000 + 0.5))
    return f"{minutes}{NBSP}min" if minutes < 60 else f"{int(minutes / 60 + 0.5)}{NBSP}h"


def invite_texts(stats: Stats | None) -> tuple[str, str]:
    """O título e a mensagem do convite (Interface): com os números do banco ou, sem eles, o título de reserva."""
    if stats is None or stats.meetings < 1:
        return INVITE_FALLBACK_TITLE, INVITE_PITCH
    title = f"O {about.APP_NAME} já guardou {plural(stats.meetings, 'reunião sua', 'reuniões suas')}"
    actions = f", e {plural(stats.actions, 'ação que não se perdeu', 'ações que não se perderam')}" if stats.actions else ""
    value = f"São {duration_text(stats.duration_ms)} de conversa que você pode buscar, ouvir e citar quando quiser{actions}."
    return title, f"{value} {INVITE_PITCH}"


def cadence_block(enabled: bool, processed: int, next_at: datetime | None, now: datetime) -> str:
    """Comportamento 1, a parte do estado: vazio quando o convite pode aparecer; senão o motivo (vai ao DEBUG)."""
    if not enabled:
        return "desligado na página Sobre"
    if processed < MIN_PROCESSED:
        return f"{plural(processed, 'reunião processada', 'reuniões processadas')}; o primeiro vem depois da 3ª"
    if next_at is not None and now < next_at:
        return f"espera até {next_at.date().isoformat()}"
    return ""


def minutes_done(job: Mapping[str, Any]) -> bool:
    """O job da LLM fez a ata (o "ata pronta" do aviso, U-020)."""
    try:
        done = json.loads(job.get("timings_json") or "{}").get("llm", {}).get("done") or []
    except (ValueError, AttributeError):
        return False
    return "minutes" in [str(step) for step in done]


class Support(QObject):
    """O apoio no app: o estado do convite, a cópia do e-mail, o navegador, os diálogos e o agradecimento. `notify` é o aviso
    do app (o controlador; None nos testes sem ele); `platform` traz o diálogo à frente (o app está em segundo plano)."""
    meeting_notified = Signal()          # Comportamento 1: o aviso de reunião processada ou de ata pronta

    def __init__(self, store: Store, notify: Callable[[str], Any] | None = None, platform: Platform | None = None, *,
                 clock: Callable[[], datetime] = local_now, open_url: Callable[[QUrl], bool] | None = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.store, self.notify, self.platform = store, notify, platform
        self.clock = clock
        self.open_url = open_url                 # None: `QDesktopServices.openUrl`, lido na hora (os testes o trocam)

    def now(self) -> datetime:
        return self.clock()

    # ---------------------------------------------------------------- estado
    def enabled(self) -> bool:
        return bool(self.store.get(SPECS[ENABLED]))

    def next_at(self) -> datetime | None:
        return parse_when(self.store.get_setting(NEXT_AT))

    def shown(self) -> int:
        """Quantas vezes o convite apareceu (0 com o banco ocupado ou fechado: só a contagem e o log a usam)."""
        try:
            return int(self.store.get(SPECS[SHOWN]))
        except Exception as e:  # o banco ocupado não derruba o convite nem o apoio
            log.debug("apoio: a contagem do convite não foi lida (%s)", type(e).__name__)
            return 0

    def _write(self, values: Mapping[str, str]) -> bool:
        """As chaves do estado juntas, numa transação (nada pela metade)."""
        try:
            with self.store.transaction():
                for key, value in values.items():
                    self.store.set_setting(key, value)
        except Exception as e:  # banco ocupado ou fechado: o convite volta no próximo aviso
            log.warning("apoio: o estado do convite não foi gravado (%s)", type(e).__name__)
            return False
        return True

    def mark_shown(self) -> int:
        """O convite apareceu: a contagem sobe e a próxima data vai a +30 dias. Devolve a contagem."""
        n = self.shown() + 1
        self._write({SHOWN: str(n), NEXT_AT: _iso(self.now() + timedelta(days=SHOWN_DAYS))})
        return n

    def defer(self, days: int, why: str) -> None:
        """"Agora não" (30 dias) ou sem resposta (7): a próxima data, contada de agora."""
        self._write({NEXT_AT: _iso(self.now() + timedelta(days=days))})
        log.info("convite de apoio adiado por %d dias (%s; mostrado %s)", days, why, plural(self.shown(), "vez", "vezes"))

    def donated(self, via: str) -> None:
        """"Já doei" ou "Pronto, doei": a data do apoio, o convite 180 dias depois (pergunta 3) e o agradecimento."""
        now = self.now()
        self._write({DONATED_AT: _iso(now), NEXT_AT: _iso(now + timedelta(days=DONATED_DAYS))})
        log.info("apoio informado (%s; convite mostrado %s); o convite volta em %d dias", via, plural(self.shown(), "vez", "vezes"),
                 DONATED_DAYS)
        if self.notify is not None:
            self.notify(THANKS)

    def job_noticed(self, job: Mapping[str, Any]) -> bool:
        """Comportamento 1: o aviso de reunião processada (processamento ou reprocessamento) ou de ata pronta agenda a
        checagem do convite (`meeting_notified`); o de outro job, não. True quando agendou."""
        kind = str(job.get("kind") or "")
        if kind in PROCESSING_KINDS or (kind == JobKind.LLM and minutes_done(job)):
            self.meeting_notified.emit()
            return True
        return False

    # ---------------------------------------------------------------- ações
    def copy_email(self, method: str) -> str:
        """"Copiar chave" (Pix) e "Copiar e-mail" (PayPal): o e-mail na área de transferência; no log, só o que foi copiado."""
        copy_rich(about.DONATION_EMAIL)
        log.info("apoio: %s para a área de transferência", "a chave do Pix copiada" if method == PIX else "o e-mail do PayPal copiado")
        return about.DONATION_EMAIL

    def open_paypal(self) -> bool:
        """"Abrir o PayPal": copia o e-mail e abre a página de enviar dinheiro no navegador, pelo Qt. True quando abriu."""
        self.copy_email(PAYPAL)
        opener = self.open_url or QDesktopServices.openUrl
        ok = bool(opener(QUrl(about.PAYPAL_URL)))
        if ok:
            log.info("apoio: o PayPal abriu no navegador")
        else:
            log.warning("apoio: o navegador não abriu o PayPal")
        return ok

    def open_choice(self, parent: QWidget | None = None, via: str = "bandeja") -> None:
        """"Apoiar o projeto…" (U-123): o diálogo pequeno com os dois caminhos; o escolhido abre o diálogo dele."""
        from .donate_dialogs import PAYPAL_RESULT, PIX_RESULT, SupportDialog

        log.info("apoio: \"Apoiar o projeto\" aberto (%s)", via)
        result = self.run(SupportDialog(parent))
        if result in (PIX_RESULT, PAYPAL_RESULT):
            self.open_pay(PIX if result == PIX_RESULT else PAYPAL, parent, via)

    def open_from_history(self) -> None:
        """U-128: o link do rodapé do Histórico (sem argumento: o `clicked` do link)."""
        self.open_choice(None, "histórico")

    def open_pay(self, method: str, parent: QWidget | None = None, via: str = "") -> bool:
        """"Doar pelo Pix" (U-124) ou "Doar pelo PayPal" (U-125), modal; True quando o usuário disse "Pronto, doei"."""
        from .donate_dialogs import DONATED_RESULT, PayDialog

        log.info("apoio: %s aberto (%s)", METHOD_NAMES[method], via)
        if self.run(PayDialog(method, self, parent)) != DONATED_RESULT:
            return False
        self.donated(f"{METHOD_NAMES[method]}, {via}")
        return True

    def run(self, dlg: QDialog) -> int:
        """O diálogo modal, à frente (pela plataforma, no giro seguinte), apagado ao fechar; separado para os testes."""
        if self.platform is not None:
            QTimer.singleShot(0, dlg, functools.partial(self._front, dlg))
        try:
            return dlg.exec()
        finally:
            dlg.deleteLater()

    def _front(self, dlg: QDialog) -> None:
        if self.platform is not None:
            with suppress(Exception):
                self.platform.bring_to_front(int(dlg.winId()))


__all__ = ["DONATED_AT", "DONATED_DAYS", "ENABLED", "EXPIRED_DAYS", "INVITE_FALLBACK_TITLE", "INVITE_PITCH", "LATER_DAYS",
           "METHOD_NAMES", "MIN_PROCESSED", "NBSP", "NEXT_AT", "PAYPAL", "PAYPAL_MONEY", "PIX", "PIX_MONEY", "SHOWN", "SHOWN_DAYS",
           "SUPPORT_HEAD", "SUPPORT_TEXT", "THANKS", "Stats", "Support", "cadence_block", "duration_text", "invite_texts", "local_now",
           "meeting_stats", "minutes_done", "parse_when"]
