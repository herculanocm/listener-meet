""""Salvar áudio…" e "Salvar vídeo com legendas…" da reunião aberta (F5.19, itens 1, 3 e 6 a 8; U-150 a U-152).

O clique confere na thread da interface o que impede a exportação (a reunião gravando, o arquivo de áudio fora da pasta
do banco, nenhum pedaço, o vídeo sem falas, outra exportação de mídia no app) e avisa na faixa da reunião sem abrir o
diálogo; lê ali as falas, monta as legendas e o desenhista. Depois do diálogo, o destino aberto num player e o pouco espaço
livre avisam antes de gerar (A-19, A-02). O arquivo sai numa `StreamTask` de prioridade normal (A-14: uma thread de
prioridade baixa que segura o GIL travaria a interface e a captura), com o corpo em `media_job.run_job`, sem Qt. O
progresso vai para uma faixa própria, com "Cancelar" (A-15: a faixa comum é escrita pelo .docx, pela LLM e pelo idioma); o
resultado ("Salvo em …", "Exportação cancelada.", os avisos) vai para a comum. Com uma gravação em curso a tarefa espera
parada (A-07). Uma exportação de mídia por app (A-17). Fechar a janela no meio pergunta; a saída do app cancela e espera.
F5.26 (D-112): no vídeo, o reprocessamento em curso e as vozes pendentes que falam seguram antes de tudo (U-178); depois, o
diálogo do texto da legenda (U-179, com o destino escolhido pelo usuário) decide entre a revisada e a original, e as legendas
vão também numa faixa de texto do MP4 (`media_video_dialog`, `media_text`). F5.27: a reunião com a tela gravada sai em
1920 x 1080, com a tela em cima e a faixa da legenda embaixo (`media_compose`); sem tela, o vídeo da F5.19."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QStandardPaths
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from ..log import get_logger
from ..model import MeetingStatus
from ..pipeline import review
from ..rag import export
from .components import InfoBar
from .media_audio import PEAK, AudioFormat, AudioPlan, Tick, available_formats, format_for, plan_audio, write_audio
from .media_compose import screen_job
from .media_cues import Speech, build_cues, screens, text_track
from .media_frames import FrameRenderer
from .media_job import WAIT, JobFailure, estimate_bytes, held_by_another, media_error_text, progress_tick, run_job, space_problem
from .media_video import video_available, write_video
from .media_video_dialog import REPROCESSING, ask_review, ask_text, blocking_voices, reprocessing, revise_cost
from .player_source import audio_path_missing
from .tasks import StreamTask, run_stream, wait_before_close
from .timeline import build_lanes, lane_of_rows

if TYPE_CHECKING:
    from ..pipeline.review import TranscriptRow
    from ..store import Store
    from .media_cues import Screen

log = get_logger("ui.media")

AUDIO, VIDEO = "audio", "video"
VIDEO_TIP = "MP4 com o áudio e a legenda de cada fala, com nome e cargo"
VIDEO_MISSING = "Indisponível: o vídeo usa o PyAV, que vem com o extra pipeline (faster-whisper)."
NO_AUDIO = "Esta reunião não tem áudio gravado."
RECORDING = "A reunião ainda está gravando: exporte depois de parar."
NO_SPEECH = "Esta reunião ainda não tem transcrição: o vídeo sai depois do processamento."
WAITING = "Na espera: uma gravação está em curso. A exportação continua quando ela parar."
MEDIA_WAIT_MS = 5000             # a saída do app espera a exportação cancelada até isto (depois avisa no log e espera)
WHAT = {AUDIO: "o áudio", VIDEO: "o vídeo"}
DOING = {AUDIO: "Gravando o áudio", VIDEO: "Gerando o vídeo"}
PEAK_SHARE = {AUDIO: 0.3, VIDEO: 0.05}  # a primeira passada (só decodificar) no tempo todo [chute pela reunião 22]
_running: dict[int, str] = {}    # A-17: a exportação de mídia em curso no app (reunião -> tipo): a interface põe, a tarefa tira


def exporting(meeting_id: int) -> bool:
    """Uma exportação de mídia desta reunião em curso (F5.27: o "Apagar a tela" fica apagado)."""
    return meeting_id in _running


def overall_percent(kind: str, stage: str, percent: int) -> int:
    """O percentual da exportação inteira (R-03), o mesmo na faixa e na pergunta do fechar: a passada do pico (só
    decodificar) numa faixa inicial, a gravação no resto."""
    share = PEAK_SHARE.get(kind, 0.1)
    return int(percent * share) if stage == PEAK else int(share * 100 + percent * (1 - share))


def audio_tip(formats: tuple[AudioFormat, ...]) -> str:
    names = [f.label.split(",")[0] for f in formats]
    listed = ", ".join(names[:-1]) + f" ou {names[-1]}" if len(names) > 1 else "".join(names)
    return f"A reunião em {listed}, como o player toca: as pausas ficam fora"


def missing_audio(store: Store, meeting_id: int) -> str | None:
    """O aviso do arquivo de áudio do mês fora da pasta do banco (o anexo o criaria vazio), ou None."""
    m = store.get_meeting(meeting_id)
    missing = audio_path_missing(store, m) if m is not None else None
    return None if missing is None else f"Sem o arquivo de áudio desta reunião na pasta do banco ({missing})."


def speeches_for(store: Store, meeting_id: int, prefer_llm: bool, rows: list[TranscriptRow] | None = None) -> list[Speech]:
    """As falas da leitura (`rows`, ou lidas agora) com o texto escolhido (a revisada, quando `prefer_llm` e ela existe; senão,
    a original), o nome da pessoa e o cargo do dia, com a cor da faixa de cada falante na Linha do tempo (U-081); sem texto, a
    fala sai."""
    rows = review.transcript_rows(store, meeting_id, prefer_llm) if rows is None else rows
    me = store.get_me()
    colors = lane_of_rows(build_lanes(rows, int(me["id"]) if me else None), len(rows))
    out: list[Speech] = []
    for row, color in zip(rows, colors, strict=True):
        text = " ".join((row.text_llm if prefer_llm and row.text_llm else row.text).split())
        if text:
            out.append(Speech(row.t_start_ms, row.t_end_ms, row.name, row.role, text, color or 0))
    return out


class MediaSaver(QObject):
    """Os itens `act_audio` e `act_video`, a faixa do progresso (`bar`, com "Cancelar") e a tarefa (`task`, None parada)."""

    def __init__(self, window: QWidget, store: Store, meeting_id: int, *, message: Callable[[str], None],
                 saved: Callable[[Path], None], prefer_llm: Callable[[], bool], recording: Callable[[], bool] | None = None,
                 on_review: Callable[[], None] | None = None, on_revise: Callable[[], None] | None = None):
        super().__init__(window)
        self.window, self.store, self.meeting_id = window, store, meeting_id
        self.message, self.saved, self.prefer_llm = message, saved, prefer_llm
        self.recording = recording or (lambda: False)
        self.on_review = on_review                       # F5.26 (U-178): abre a revisão de falantes
        self.on_revise = on_revise                       # F5.26 (U-179): o pedido de "Revisar a transcrição com IA"
        self.held: Callable[[], str | None] = lambda: None   # F5.27: o aviso que segura a exportação (a tela sendo apagada)
        self.formats = available_formats()
        self.video_ready = video_available()             # procura sem importar
        self.act_audio = QAction("Salvar áudio…", window)
        self.act_audio.setToolTip(audio_tip(self.formats))
        self.act_audio.triggered.connect(self._audio_clicked)
        self.act_video = QAction("Salvar vídeo com legendas…", window)
        self.act_video.setToolTip(VIDEO_TIP if self.video_ready else VIDEO_MISSING)
        self.act_video.triggered.connect(self._video_clicked)
        self.bar = InfoBar("info", "", "Cancelar", window)
        self.bar.label.setObjectName("mediaProgress")
        self.bar.action.clicked.connect(self.cancel)
        self.bar.setVisible(False)
        self.task: StreamTask | None = None
        self.kind = ""
        self.target = ""
        self.percent = 0
        self._enable(True)

    def _enable(self, idle: bool) -> None:
        self.act_audio.setEnabled(idle and bool(self.formats))
        self.act_video.setEnabled(idle and self.video_ready)
        self.bar.setVisible(not idle)

    @property
    def busy(self) -> bool:
        return self.task is not None

    def _audio_clicked(self) -> None:
        self.save_audio()

    def _video_clicked(self) -> None:
        self.save_video()

    # ---------------------------------------------------------------- conferir e perguntar
    def _plan(self) -> tuple[AudioPlan, dict[str, Any]] | None:
        """O plano do áudio e a reunião, ou None com o aviso na faixa (sem abrir o diálogo)."""
        if _running:
            other = next(iter(_running))
            self.message(f"Outra exportação de áudio ou vídeo está em curso (reunião {other}): espere terminar ou cancele lá.")
            return None
        m = self.store.get_meeting(self.meeting_id)
        if m is None:
            self.message("Esta reunião não existe mais.")
            return None
        held = self.held()
        if held:
            self.message(held)
            return None
        if m.get("status") in (MeetingStatus.RECORDING, MeetingStatus.PAUSED):
            self.message(RECORDING)
            return None
        problem = missing_audio(self.store, self.meeting_id)
        if problem is not None:
            self.message(problem)
            return None
        plan = plan_audio(self.store, self.meeting_id)
        if plan is None or plan.frames <= 0:
            self.message(NO_AUDIO)
            return None
        return plan, dict(m)

    def _default_path(self, m: dict[str, Any], ext: str) -> str:
        """"AAAA-MM-DD título.ext" em Documentos, como o .md e o .docx."""
        data = export.ExportMeeting(self.meeting_id, str(m.get("title") or m.get("title_llm") or ""), str(m.get("started_at") or ""),
                                    m.get("duration_ms"))
        folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation) or str(Path.home())
        return str(Path(folder) / export.default_filename(data, ext))

    def _ask_audio(self, m: dict[str, Any]) -> tuple[str, AudioFormat] | None:
        filters = [f.dialog_filter for f in self.formats]
        path, chosen = QFileDialog.getSaveFileName(self.window, "Salvar áudio", self._default_path(m, self.formats[0].ext),
                                                   ";;".join(filters))
        if not path:
            return None
        fmt = self.formats[filters.index(chosen)] if chosen in filters else self.formats[0]
        return format_for(path, fmt, self.formats)

    def _ask_video(self, m: dict[str, Any]) -> str | None:
        path, _ = QFileDialog.getSaveFileName(self.window, "Salvar vídeo com legendas", self._default_path(m, ".mp4"),
                                              "Vídeo MP4 (*.mp4)")
        return path or None

    def _video_held(self) -> bool:
        """F5.26: o reprocessamento em curso avisa na faixa; uma voz pendente que fala no vídeo mostra o U-178, que só leva à
        revisão (sem "salvar mesmo assim"). True segura o vídeo."""
        if reprocessing(self.store, self.meeting_id):
            self.message(REPROCESSING)
            return True
        n = blocking_voices(self.store, self.meeting_id)
        if n and ask_review(self.window, n, self.on_review is not None) and self.on_review is not None:
            self.on_review()
        return n > 0

    def _blocked(self, target: Path, need: int) -> bool:
        """Depois do diálogo, antes de gerar: o destino aberto num player (o aviso do U-030) ou pouco espaço livre."""
        if held_by_another(target):
            QMessageBox.warning(self.window, "Exportar", media_error_text(target, PermissionError(13, "em uso")))
            return True
        problem = space_problem(target, need)
        if problem is not None:
            QMessageBox.warning(self.window, "Exportar", problem)
            return True
        return False

    # ---------------------------------------------------------------- exportar
    def save_audio(self, path: str | None = None, fmt: AudioFormat | None = None) -> StreamTask | None:
        """Grava o áudio em `path` no formato `fmt` (ou no que o usuário escolher); None sem destino, com outra exportação
        em curso ou com o aviso do que impede."""
        if self.task is not None or not self.formats:
            return None
        checked = self._plan()
        if checked is None:
            return None
        plan, m = checked
        if path is None:
            asked = self._ask_audio(m)
            if asked is None:
                return None
            path, fmt = asked
        else:
            path, fmt = format_for(path, fmt or self.formats[0], self.formats)
        target, chosen, store = Path(path), fmt, self.store     # a tarefa não toca a janela, que pode fechar no meio
        if self._blocked(target, estimate_bytes(False, chosen.key, plan)):
            return None

        def make(tick: Tick) -> tuple[Path, int, int]:
            return write_audio(store, plan, target, chosen, tick), 0, 0

        return self._start(AUDIO, chosen.key, target, plan, make)

    def _ask_text(self, rows: list[TranscriptRow]) -> bool | None:
        """U-179: True a revisada, False a original, None cancelou (ou pediu a revisão, que segue sozinha)."""
        revised = any(r.text_llm for r in rows)
        return ask_text(self.window, revised=revised, prefer_llm=self.prefer_llm(), on_generate=self.on_revise,
                        cost=None if revised else revise_cost(self.store, self.meeting_id))

    def save_video(self, path: str | None = None, prefer_llm: bool | None = None) -> StreamTask | None:
        """Gera o MP4 em `path` com o texto de `prefer_llm` (ou, sem `path`, o texto e o destino que o usuário escolher; com
        `path` e sem `prefer_llm`, o que a Transcrição mostra); None como no `save_audio`, sem falas ou com a guarda da F5.26."""
        if self.task is not None or not self.video_ready:
            return None
        checked = self._plan()
        if checked is None:
            return None
        plan, m = checked
        rows = review.transcript_rows(self.store, self.meeting_id)
        if not speeches_for(self.store, self.meeting_id, self.prefer_llm(), rows):
            self.message(NO_SPEECH)
            return None
        if self._video_held():
            return None
        use_llm = prefer_llm if prefer_llm is not None else (self._ask_text(rows) if path is None else self.prefer_llm())
        if use_llm is None:
            return None
        speeches = speeches_for(self.store, self.meeting_id, use_llm, rows)
        if not speeches:
            self.message(NO_SPEECH)
            return None
        cues = build_cues(speeches, plan.pauses, plan.duration_ms)
        timeline: list[tuple[int, Screen]] = screens(cues, plan.duration_ms)
        title = str(m.get("title") or m.get("title_llm") or f"Reunião {self.meeting_id}")
        date = export.fmt_date(m.get("started_at"))
        renderer = FrameRenderer(title, date, cues)
        with_screen = screen_job(self.store, plan, title, date, cues)      # F5.27: com a tela gravada, 1920 x 1080
        path = path or self._ask_video(m)
        if not path:
            return None
        target, store = Path(path if path.lower().endswith(".mp4") else path + ".mp4"), self.store
        if self._blocked(target, estimate_bytes(True, "mp4", plan) + (with_screen.extra_bytes if with_screen else 0)):
            return None

        captions = text_track(cues, plan.duration_ms)  # F5.26: a faixa de texto, com as mesmas legendas

        def make(tick: Tick) -> tuple[Path, int, int]:
            if with_screen is not None:
                out, frames = with_screen.write(target, tick, timeline, captions)
            else:
                out, frames = write_video(store, plan, timeline, renderer.render, target, tick, (renderer.width, renderer.height),
                                          captions)
            return out, len(cues), frames

        return self._start(VIDEO, "mp4", target, plan, make)

    def _start(self, kind: str, fmt: str, target: Path, plan: AudioPlan, make: Callable[[Tick], tuple[Path, int, int]]) -> StreamTask:
        meeting_id, store, recording, seconds = self.meeting_id, self.store, self.recording, plan.duration_ms / 1000

        def work(_emit: Callable[[str], None], cancelled: Callable[[], bool], report: Callable[[Any], None]) -> object:
            try:
                return run_job(kind, fmt, meeting_id, target, make, lambda: missing_audio(store, meeting_id),
                               progress_tick(cancelled, report, recording), seconds)
            finally:
                _running.pop(meeting_id, None)           # a própria tarefa solta a vaga: vale com a janela já fechada

        log.info("reunião %s: exportando %s (%s, %.1f s de áudio)", meeting_id, kind, fmt, seconds)
        self.kind, self.target, self.percent = kind, str(target), 0
        self._enable(False)
        self.bar.set_text(f"Preparando {WHAT[kind]}…")
        _running[meeting_id] = kind                      # antes de a tarefa nascer: uma tarefa rápida não deixa a vaga presa
        self.task = run_stream(work, self.store, lambda _text: None, self._done, self._progress)
        return self.task

    def _progress(self, step: object) -> None:
        with suppress(RuntimeError):                     # a janela já foi destruída
            if self.task is not None and self.task.cancelled:     # R-02: o "Cancelando…" fica até o fim
                return
            stage, percent = step if isinstance(step, tuple) else ("", 0)
            if stage == WAIT:
                self.bar.set_text(WAITING)
                return
            self.percent = overall_percent(self.kind, str(stage), int(percent))
            doing = "Preparando o áudio" if stage == PEAK else DOING.get(self.kind, "Exportando")
            self.bar.set_text(f"{doing}… {self.percent} %")

    def _done(self, result: object) -> None:
        """O fim da tarefa com a janela aberta: "Salvo em …", o aviso na faixa ou a caixa do erro."""
        with suppress(RuntimeError):                     # a janela já foi destruída: o log já foi escrito na tarefa
            self.task = None
            self._enable(True)
            if isinstance(result, Path):
                self.saved(result)
                return
            failure = result if isinstance(result, JobFailure) else JobFailure(dialog=f"Não consegui gerar {WHAT[self.kind]}.")
            self.message(failure.notice)
            if failure.dialog:
                QMessageBox.warning(self.window, "Exportar", failure.dialog)

    # ---------------------------------------------------------------- cancelar, fechar e sair
    def cancel(self) -> None:
        if self.task is not None:
            self.task.cancel()
            self.bar.set_text("Cancelando…")

    def confirm_close(self) -> bool:
        """Fechar a janela com uma exportação em curso: True fecha (a exportação foi cancelada); False fica."""
        if self.task is None:
            return True
        text = (f"{WHAT[self.kind].capitalize()} desta reunião ainda está sendo gerado ({self.percent} %). Fechar a janela cancela "
                "a exportação.")
        if not self.ask_close(text):
            return False
        self.cancel()
        return True

    def ask_close(self, text: str) -> bool:
        """A pergunta do fechar: "Continuar gerando" (padrão e Esc) ou "Cancelar e fechar" (True)."""
        box = QMessageBox(QMessageBox.Icon.Question, "Exportar", text, parent=self.window)
        keep = box.addButton("Continuar gerando", QMessageBox.ButtonRole.RejectRole)
        stop = box.addButton("Cancelar e fechar", QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(keep)
        box.setEscapeButton(keep)
        box.exec()
        return box.clickedButton() is stop

    def shutdown(self) -> None:
        """Saída do app e exclusão da reunião: cancela e espera a tarefa antes de o banco fechar (o temporário sai na própria
        tarefa); zera a `task` para o fechar não perguntar depois (A-16)."""
        task, self.task = self.task, None
        if task is not None:
            task.cancel()
            wait_before_close(task, MEDIA_WAIT_MS, f"a exportação da reunião {self.meeting_id}")


__all__ = ["AUDIO", "NO_AUDIO", "NO_SPEECH", "RECORDING", "VIDEO", "VIDEO_MISSING", "VIDEO_TIP", "WAITING", "MediaSaver", "audio_tip",
           "exporting", "missing_audio", "overall_percent", "speeches_for"]
