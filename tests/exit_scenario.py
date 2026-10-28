"""F5 R7-03: a saída do app com a rede presa termina com código 0, sem "QThread: Destroyed while thread is still running".
Roda num processo à parte (`tests/test_rag_ui.py` confere o código de saída; também roda sozinho:
`python tests/exit_scenario.py [--glossary]`). Com o fake da LLM mais lento que qualquer espera da saída (`DELAY_S`), deixa
em voo o vetor da consulta de uma busca do Histórico (Enter), uma pergunta do chat global e uma do chat da reunião aberta
antes do primeiro pedaço, e sai pelo caminho do app (`aboutToQuit` -> `emergency_stop`, que fecha o banco). Sem o
cancelamento de verdade, as `QThread`s seguiam vivas depois do `store.close()` e o processo abortava (134 no Linux).
`--glossary` (R8-04) deixa também o glossário do warm-up em voo, pedido pelo próprio diálogo numa `Task` comum. Nada de
rede, áudio ou chave real: `LISTENER_LLM=fake` e a plataforma simulada dos testes."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["LISTENER_LLM"] = "fake"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))     # o pacote deste checkout, não o instalado

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

DELAY_S = 60.0                   # o fake demora mais que todas as esperas da saída juntas
QUIT_AFTER_MS = 800              # tempo para as três chamadas ficarem presas no fake


def main() -> int:
    app = QApplication([])
    from listener.llm import fake
    from listener.model import HEAVY_JOB_KINDS, JobStatus
    from listener.rag import index
    from listener.store import Store
    from listener.store.segments import SegmentInsert
    from listener.ui.controller import Controller
    from tests.fake_platform import FakePlatform

    root = Path(tempfile.mkdtemp(prefix="listener-exit-"))
    store = Store(root / "db")
    store.upsert_me("Eu")
    ana = store.people.create_person("Ana", role="CFO")
    mid = store.create_meeting("Orçamento 2027", None, None, None)
    store.finish_meeting(mid, 120_000)
    job = store.jobs.enqueue(mid)
    store.jobs.claim(pid=1, kinds=HEAVY_JOB_KINDS)
    store.jobs.finish(job, JobStatus.DONE)
    store.segments.insert_segments([SegmentInsert(mid, job, "R", 0, 30_000, "Precisamos aprovar a auditoria até sexta.", "pyannote",
                                                  "final", person_id=ana),
                                    SegmentInsert(mid, job, "R", 30_000, 60_000, "O prazo do contrato vence na semana que vem.",
                                                  "pyannote", "final", person_id=ana)])
    store.providers.update_provider("gemini", api_key="AIzaSy-chave-de-teste-ABCDEFGH12345678", fast_model="fake-fast",
                                    strong_model="fake-strong")
    index.sync_text(store, mid)
    index.fill_vectors(store, mid, provider=fake.FakeProvider())   # vetores prontos: o Enter pede o vetor da consulta

    real_init = fake.FakeProvider.__init__

    def slow_init(self: fake.FakeProvider, *args: object, **kwargs: object) -> None:
        real_init(self, *args, **kwargs)  # type: ignore[arg-type]
        self.delay_s = DELAY_S          # a "rede" presa: nenhuma resposta, nenhum pedaço

    fake.FakeProvider.__init__ = slow_init  # type: ignore[method-assign]
    platform = FakePlatform(root)
    from listener.ui import theme

    theme.apply(app, platform)                               # F5.4 (A-09): como no app, com o estilo em Python até a saída
    c = Controller(app, platform, store)
    c.tray = None
    app.aboutToQuit.connect(c.emergency_stop)
    if "--glossary" in sys.argv:                             # R8-04: o glossário do warm-up preso na rede, numa `Task` comum
        from listener.ui.warmup import WarmupDialog

        warmup = WarmupDialog(platform, store)
        warmup.title_edit.setText("Orçamento 2027")
        warmup.participants_edit.setText("Ana, Bia")
        warmup.request_glossary()                            # o pedido do próprio diálogo (com o cancelamento da `Task`)
        warmup.reject()                                      # o diálogo fecha; a chamada segue em voo, como no app
        warmup.deleteLater()
    page = c.open_history().page
    page.search.setText("auditoria")
    page._search_now()                                       # Enter: busca por sentido presa no vetor da consulta
    page.question.setText("o que foi decidido sobre a auditoria?")
    page.ask()                                               # chat global preso
    win = c.open_meeting(mid)
    win.chat.question.setText("qual é o prazo?")
    win.chat.ask()                                           # chat da reunião sem o primeiro pedaço
    t0 = time.monotonic()
    QTimer.singleShot(QUIT_AFTER_MS, app.quit)
    code = app.exec()
    theme.release(app)                                       # A-09 (R-06): como o `run` do app, depois do laço
    sys.stderr.write(f"app.exec() voltou {code} em {time.monotonic() - t0:.1f} s; o processo sai agora\n")
    if "--glossary" in sys.argv:                             # o desfecho do glossário ficou no banco (já fechado pela saída)
        with Store(root / "db") as check:
            errors = [str(r[0]) for r in check._q("SELECT error FROM llm_calls WHERE task = 'glossary'")]
        sys.stderr.write(f"glossário em llm_calls: {errors}\n")
    shutil.rmtree(root, ignore_errors=True)                  # a pasta temporária deste script (o banco já fechou)
    return code


if __name__ == "__main__":
    sys.exit(main())
