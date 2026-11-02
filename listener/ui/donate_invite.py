"""F5.12 L2 (U-126, U-127): a cola do convite para apoiar, na thread da interface: o apoio (`ui/donate.py`), o cartão
(`ui/donate_card.py`), a F5.7 (a chamada detectada e o cartão dela) e a pilha dos avisos.

- O `Support.meeting_notified` (o aviso de reunião processada ou de ata pronta) agenda a checagem para 8 s depois
  (Comportamento 1; um aviso novo recomeça a conta). Ela só mostra com o app parado (sem gravação, pausa ou salvamento; sem
  o "Antes de gravar" e fora do assistente; sem chamada: a detectada pela F5.7 e, R-02, qualquer app com o microfone em uso
  no indicador do sistema, também com a F5.7 desligada ou sem avisar para aquele app), sem diálogo modal e sem a revisão de
  falantes à vista (ela abre sozinha depois do processamento) e com a cadência do `Support` (ligado, 3 ou mais reuniões
  processadas, `donate.next_at` vencido). O motivo do "não" vai ao DEBUG.
- O cartão sai da captura de tela antes de aparecer (A-13); sem a exclusão, ele não aparece e o estado não muda (fica para o
  próximo aviso; o primeiro "não" da sessão vai ao log). Mostrado, a contagem sobe e a próxima data vai a +30 dias.
- À vista (conferido a cada 1 s): a gravação, uma chamada, o microfone em uso ou o "Antes de gravar" o fecham e adiam 30
  dias; o interruptor desligado na página Sobre o fecha sem mexer no estado (CP8); o apoio informado por qualquer entrada o
  fecha sem adiar (R-01). A pilha dos avisos o evita como evita o cartão da F5.7 (`avoid_rect`): os dois nunca ficam juntos
  à vista.
- As respostas: "Agora não", o ✕ e sumir em 2 min adiam 30 dias; "Já doei" agradece e adia 180; "Doar … pelo Pix" e
  "… pelo PayPal" fecham o cartão e abrem o diálogo do meio de pagamento no giro seguinte (é modal; Comportamento 3).
  Nenhuma chamada de rede.
- Saída (`shutdown`, no `aboutToQuit`): o cartão some sem mexer no banco (a data já foi a +30 dias ao aparecer)."""
from __future__ import annotations

import functools
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication, QWizard

from ..fmt import plural
from ..log import get_logger
from .call_answers import open_warmup_dialog
from .donate import EXPIRED_DAYS, LATER_DAYS, METHOD_NAMES, PAYPAL, PIX, Stats, Support, cadence_block, invite_texts, meeting_stats
from .donate_card import InviteCard
from .review import ReviewDialog

if TYPE_CHECKING:
    from ..platform.base import Platform
    from ..store import Store

log = get_logger("ui.donate")

type Rect = tuple[int, int, int, int]
INVITE_DELAY_MS = 8_000          # Comportamento 1: a checagem, 8 s depois do aviso
WATCH_MS = 1_000                 # à vista: a gravação, uma chamada, o microfone ou o "Antes de gravar" o fecham


class InviteHost(Protocol):
    """O que a cola usa do controlador (os testes passam um falso): `busy` (gravando, pausado, salvando ou saindo) e a
    pilha dos avisos."""
    toasts: Any

    @property
    def busy(self) -> bool: ...


def _never() -> bool:
    return False


def _no_rect() -> Rect | None:
    return None


def wizard_open() -> bool:
    """O assistente de primeira inicialização à vista (o convite nunca aparece nele)."""
    return any(isinstance(w, QWizard) and w.isVisible() for w in QApplication.topLevelWidgets())


def screen_busy() -> str:
    """Só na checagem: um diálogo modal (o cartão não receberia o clique) ou a revisão de falantes à vista, que abre sozinha
    depois do processamento (o convite não disputa a atenção com ela). Vazio quando nada disso."""
    if QApplication.activeModalWidget() is not None:
        return "um diálogo está aberto"
    if any(isinstance(w, ReviewDialog) and w.isVisible() for w in QApplication.topLevelWidgets()):
        return "a revisão de falantes está aberta"
    return ""


class DonateInvite(QObject):
    """A cola do convite. `in_call` é a F5.7 (`CallWatch.in_call`); `other_rect`, o cartão dela, que a pilha dos avisos
    também evita. `start` depois do `start` da F5.7 (o `avoid_also` fica com os dois); `shutdown` na saída."""

    def __init__(self, host: InviteHost, platform: Platform, store: Store, support: Support, *,
                 in_call: Callable[[], bool] = _never, other_rect: Callable[[], Rect | None] = _no_rect,
                 card_factory: Callable[..., InviteCard] = InviteCard, delay_ms: int = INVITE_DELAY_MS,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.host, self.platform, self.store, self.support = host, platform, store, support
        self.in_call, self.other_rect, self.card_factory = in_call, other_rect, card_factory
        self.card: InviteCard | None = None
        self._closed = self._exclusion_warned = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(delay_ms)
        self._timer.timeout.connect(self.check)
        self._watch = QTimer(self)
        self._watch.setInterval(WATCH_MS)
        self._watch.timeout.connect(self._watch_tick)
        support.meeting_notified.connect(self.schedule)
        support.donation_informed.connect(self._on_donation_informed)

    def start(self) -> None:
        """A pilha dos avisos passa a evitar o convite e o cartão da F5.7."""
        self.host.toasts.avoid_also(self.avoid_rect)

    def schedule(self) -> None:
        """O aviso de reunião processada ou de ata pronta: a checagem em 8 s (um aviso novo recomeça a conta)."""
        if not self._closed:
            self._timer.start()

    def pending(self) -> bool:
        """Instrumentação dos testes: a checagem está agendada."""
        return self._timer.isActive()

    # ---------------------------------------------------------------- a checagem (Comportamento 1)
    def blocked(self) -> str:
        """O app não está parado: o motivo (vazio = parado)."""
        if self.host.busy:
            return "gravando, salvando ou saindo"
        if open_warmup_dialog() is not None:
            return "o \"Antes de gravar\" está aberto"
        if wizard_open():
            return "o assistente está aberto"
        if self.in_call():
            return "chamada detectada"
        return self._mic_in_use()

    def _mic_in_use(self) -> str:
        """R-02: algum app com o microfone em uso no indicador do sistema é uma chamada provável, também a que a F5.7 não
        olha (desligada, ou sem avisar para aquele app). Sem indicador nesta plataforma (None), nada; a leitura que falha
        segura o convite (na dúvida, não aparece)."""
        try:
            users = self.platform.mic_users()
        except Exception as e:  # o registro ilegível não derruba o convite
            return f"o indicador de microfone não respondeu ({type(e).__name__})"
        return f"microfone em uso ({plural(len(users), 'app', 'apps')})" if users else ""

    def check(self) -> bool:
        """Mostra o convite quando tudo deixa; True quando ele apareceu."""
        if self._closed or self.card is not None:
            return False
        why = self.blocked() or screen_busy()
        stats: Stats | None = None
        if not why:
            stats, why = self._state()
        if why:
            log.debug("convite de apoio: não agora (%s)", why)
            return False
        return self._show(stats)

    def _state(self) -> tuple[Stats | None, str]:
        """Os números e a cadência; com o banco ocupado, o motivo (sem os números não dá para conferir a 3ª reunião: fica para
        o próximo aviso)."""
        try:
            stats = meeting_stats(self.store)
            return stats, cadence_block(self.support.enabled(), stats.meetings, self.support.next_at(), self.support.now())
        except Exception as e:  # o convite nunca derruba o app
            return None, f"o banco não respondeu ({type(e).__name__})"

    def _show(self, stats: Stats | None) -> bool:
        card = self.card_factory(*invite_texts(stats))
        if not self._exclude(card):                  # antes do primeiro `show` (A-13): nada vaza para a captura
            card.finish()
            return False
        self.card = card
        card.pix_clicked.connect(self._on_pix)
        card.paypal_clicked.connect(self._on_paypal)
        card.later.connect(self._on_later)
        card.donated.connect(self._on_donated)
        card.expired.connect(self._on_expired)
        card.place()
        card.show()
        card.announce()
        card.start()
        self.host.toasts.relayout()
        self._watch.start()
        n = self.support.mark_shown()
        log.info("convite de apoio mostrado (vez %d; %s)", n,
                 plural(stats.meetings if stats is not None else 0, "reunião processada", "reuniões processadas"))
        return True

    def _exclude(self, card: InviteCard) -> bool:
        """A-13: o cartão fora da captura de tela; o primeiro "não" da sessão vai ao log."""
        try:
            ok = bool(self.platform.exclude_from_capture(int(card.winId())))
        except Exception as e:
            log.debug("convite de apoio: exclusão da captura (%s)", type(e).__name__)
            ok = False
        if not ok and not self._exclusion_warned:
            self._exclusion_warned = True
            log.warning("convite de apoio: a janela não saiu da captura de tela; ele fica para o próximo aviso")
        return ok

    # ---------------------------------------------------------------- as respostas
    def card_rect(self) -> Rect | None:
        card = self.card
        if card is None or not card.isVisible():
            return None
        g = card.frameGeometry()
        return g.x(), g.y(), g.width(), g.height()

    def avoid_rect(self) -> Rect | None:
        """O retângulo que a pilha dos avisos evita: o convite ou, sem ele, o cartão da F5.7 (os dois nunca juntos)."""
        return self.card_rect() or self.other_rect()

    def _close(self) -> None:
        card, self.card = self.card, None
        self._watch.stop()
        if card is not None:
            card.finish()
            self.host.toasts.relayout()

    def _on_later(self) -> None:
        self._close()
        self.support.defer(LATER_DAYS, "Agora não")

    def _on_expired(self) -> None:
        self._close()
        self.support.defer(EXPIRED_DAYS, "sem resposta")

    def _on_donated(self) -> None:
        self._close()
        self.support.donated("Já doei, convite")

    def _on_donation_informed(self) -> None:
        """R-01: "Pronto, doei" ou "Já doei" por outra entrada (bandeja, Sobre, Histórico, Configurações) com o cartão à
        vista: ele fecha sem adiar (os 180 dias da doação ficam)."""
        if self.card is not None:
            self._close()
            log.info("convite de apoio: fechado pelo apoio informado")

    def _on_pix(self) -> None:
        self._choose(PIX)

    def _on_paypal(self) -> None:
        self._choose(PAYPAL)

    def _choose(self, method: str) -> None:
        """Comportamento 3: o cartão fecha e o diálogo do meio de pagamento abre no giro seguinte (fora do clique: é modal);
        a próxima data fica a da aparição (+30 dias)."""
        self._close()
        log.info("convite de apoio: %s escolhido", METHOD_NAMES[method])
        QTimer.singleShot(0, self, functools.partial(self.support.open_pay, method, None, "convite"))

    def _watch_tick(self) -> None:
        """À vista: o interruptor desligado fecha o convite sem mexer no estado (CP8); a gravação, uma chamada, o microfone
        em uso ou o "Antes de gravar" o fecham e adiam 30 dias (como sem resposta)."""
        if self.card is None:
            return
        if not self._switched_on():
            self._close()
            log.info("convite de apoio: fechado (desligado na página Sobre)")
            return
        why = self.blocked()
        if why:
            self._close()
            self.support.defer(EXPIRED_DAYS, f"fechado: {why}")

    def _switched_on(self) -> bool:
        """`donate.enabled`, relido a cada segundo com o cartão à vista (vale já, CP8); o banco ocupado não o fecha."""
        try:
            return self.support.enabled()
        except Exception as e:  # banco ocupado: segue à vista; o próximo segundo relê
            log.debug("convite de apoio: o interruptor não foi lido (%s)", type(e).__name__)
            return True

    def shutdown(self) -> None:
        """A saída do app: o convite some sem mexer no estado (a data já foi a +30 dias ao aparecer)."""
        if self._closed:
            return
        self._closed = True
        self._timer.stop()
        if self.card is not None:
            log.info("convite de apoio: fechado na saída do app")
        self._close()


__all__ = ["INVITE_DELAY_MS", "WATCH_MS", "DonateInvite", "InviteHost", "wizard_open"]
