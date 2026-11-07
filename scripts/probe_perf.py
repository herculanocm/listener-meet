"""F5.18 lote 0 — sondas de performance (spec specs/features/F5.18-performance.md, aprovada em 07/11/2026, D-096). Não fazem
parte do produto. Rodam no venv do Windows (`.venv/Scripts/python.exe`), fora de gravação: antes de qualquer medida a sonda
confere no banco real (só leitura, `mode=ro`) que nenhuma reunião está `gravando` e nenhum job `rodando` tem heartbeat
recente (`--no-quiet-check` desliga). O banco real nunca é escrito; o token do Hugging Face vem dele e nunca é impresso.

  python scripts/probe_perf.py threads  --meeting ID [--threads 4,6,8,10,12,14] [--kmp0] [--what diar,whisper]   M-01
  python scripts/probe_perf.py diar     --meeting ID                                                             M-02
  python scripts/probe_perf.py batched  --meeting ID [--batch 4,8]                                               M-03
  python scripts/probe_perf.py priority --meeting ID [--repeat 10] [--seconds 120] [--load-threads 12]           M-04 (só Windows)
  python scripts/probe_perf.py echo     [--meeting ID] [--block-s 10] [--corr-min 0.25]                          M-05
  python scripts/probe_perf.py pack     --meeting ID                                                             M-06
  python scripts/probe_perf.py embed    --meeting ID                                                             M-07
  python scripts/probe_perf.py weights  --meeting ID                                                             M-08
  python scripts/probe_perf.py sqlite   [--n 60000] [--repeat 5]                                                 M-09
  python scripts/probe_perf.py tls                                                                               M-10
  python scripts/probe_perf.py replay   --meeting ID [--speed 1.0] [--load 0.85] [--names]                       PF-09

Opções comuns: `--out PASTA` (um JSON por rodada, `<sonda>-<data-hora>.json`; padrão `%LOCALAPPDATA%/listener-meet/perf-out`,
criada se faltar), `--runs N` (3; `diar` e `weights` 1), `--minutes M` (corte do áudio, 5; `echo` e `replay` 0 = a reunião
inteira), `--audio ARQ` (wav/flac; estéreo dá L e R) OU `--meeting ID` (o áudio real do banco, `decode_range` em `mode=ro`,
canal `--channel R`), `--p-cores`/`--no-p-cores` (prende aos núcleos P pela máscara de `probe_ui.performance_mask`; ligado
por padrão só nas sondas de uma ou duas threads: echo, pack, embed, sqlite e tls; nas de 12 threads e nas de disputa fica
desligado, senão a medida não representa o worker), `--torch-threads N` (o padrão da fórmula `cpu_count - 2`, lido antes de
importar numpy e torch), `--language` (padrão: o da reunião). Cada JSON traz o subcomando, os parâmetros, `platform.machine`,
a máscara de CPU, `OMP_NUM_THREADS`, `KMP_BLOCKTIME`, as versões (torch, ctranslate2, faster-whisper, pyannote.audio,
onnxruntime) e os resultados; na saída padrão, uma linha JSON por resultado. Variantes sempre intercaladas (A, B, C, A, B,
C...); o resumo é o MENOR das medianas por variante. Variante que precisa de ambiente de processo (threads do OpenMP,
classe de prioridade, `KMP_BLOCKTIME`) roda num filho (`_child`, interno). D-059: nenhum título, texto de legenda, nome ou
caminho com a pasta do usuário sai nos JSON; as reuniões só pelo id.
"""
from __future__ import annotations

import argparse
import contextlib
import os
import sys
import warnings
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))                     # o pacote deste checkout (o .pth do venv pode apontar para outro)

AUDIO_PROBES = frozenset({"threads", "diar", "batched", "priority", "pack", "embed", "weights"})
P_CORES_DEFAULT = frozenset({"echo", "pack", "embed", "sqlite", "tls"})
TORCH_PROBES = frozenset({"diar", "batched", "embed", "weights"})     # rodam torch no próprio processo: ambiente antes de importar


def default_threads() -> int:
    return max(1, (os.cpu_count() or 2) - 2)


def add_common(p: argparse.ArgumentParser, name: str, runs: int = 3, minutes: float = 5.0) -> None:
    p.add_argument("--out", default=None, help="pasta dos JSON (padrão: %%LOCALAPPDATA%%/listener-meet/perf-out)")
    p.add_argument("--runs", type=int, default=runs, help=f"rodadas intercaladas (padrão {runs})")
    p.add_argument("--minutes", type=float, default=minutes, help=f"corte do áudio em minutos (padrão {minutes:g}; 0 = inteiro)")
    p.add_argument("--audio", default=None, help="arquivo wav/flac no lugar da reunião (estéreo: L e R)")
    p.add_argument("--meeting", type=int, default=None, help="id da reunião do banco real (áudio decodificado em mode=ro)")
    p.add_argument("--channel", default="R", choices=("L", "R"), help="canal medido da reunião (padrão R)")
    p.add_argument("--db", default=None, help="pasta do banco real (padrão: o ponteiro db-location.json); só leitura")
    p.add_argument("--p-cores", dest="p_cores", action=argparse.BooleanOptionalAction, default=name in P_CORES_DEFAULT,
                   help="prende aos núcleos P (GetSystemCpuSetInformation)")
    p.add_argument("--quiet-check", dest="quiet_check", action=argparse.BooleanOptionalAction, default=True,
                   help="recusa rodar com reunião gravando ou job rodando")
    p.add_argument("--torch-threads", type=int, default=default_threads(), help="threads do torch e do OpenMP nas sondas do pesado")
    p.add_argument("--language", default=None, help="código do Whisper (padrão: o da reunião ou o do app)")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("threads", help="M-01: RTF da diarização e do Whisper por nº de threads (um filho por valor)")
    add_common(p, "threads")
    p.add_argument("--threads", default="4,6,8,10,12,14", help="valores de OMP_NUM_THREADS/cpu_threads (padrão 4,6,8,10,12,14)")
    p.add_argument("--kmp0", action="store_true", help="acrescenta a variante 12 threads com KMP_BLOCKTIME=0 e OMP_WAIT_POLICY=PASSIVE")
    p.add_argument("--what", default="diar,whisper", help="o que medir: diar, whisper ou os dois (padrão)")
    add_common(sub.add_parser("diar", help="M-02: etapas, cadência do hook e pares vazios da diarização"), "diar", runs=1)
    p = sub.add_parser("batched", help="M-03: Whisper sequencial contra BatchedInferencePipeline, três prompts")
    add_common(p, "batched")
    p.add_argument("--batch", default="4,8", help="tamanhos de lote (padrão 4,8)")
    p = sub.add_parser("priority", help="M-04 (só Windows): o small sob carga sintética por classe de prioridade; heartbeat esfomeado")
    add_common(p, "priority")
    p.add_argument("--repeat", type=int, default=10, help="janelas de 10 s por medição (padrão 10)")
    p.add_argument("--seconds", type=float, default=120.0, help="teto em segundos de cada medição sob carga (padrão 120)")
    p.add_argument("--load-threads", type=int, default=12, help="threads do filho de carga (padrão 12)")
    p.add_argument("--load-fraction", type=float, default=1.0, help="fração de ocupação de cada thread da carga (padrão 1,0)")
    p = sub.add_parser("echo", help="M-05: fração de blocos do L que são eco do R, por reunião do banco real")
    add_common(p, "echo", minutes=0.0)
    p.add_argument("--block-s", type=float, default=10.0, help="tamanho do bloco em segundos (padrão 10)")
    p.add_argument("--corr-min", type=float, default=None, help="limiar de correlação (padrão: capture.echo_corr_min do banco)")
    add_common(sub.add_parser("pack", help="M-06: 1 a 4 janelas do small separadas contra juntas num passe"), "pack")
    add_common(sub.add_parser("embed", help="M-07: o embedding wespeaker de 5 s com torch em 1 thread"), "embed")
    add_common(sub.add_parser("weights", help="M-08: pesos e cossenos do embedding do community-1 contra o wespeaker avulso"), "weights", runs=1)
    p = sub.add_parser("sqlite", help="M-09: consultas do rag_probe com e sem cache_size/mmap_size")
    add_common(p, "sqlite")
    p.add_argument("--n", type=int, default=60_000, help="trechos e vetores sintéticos (padrão 60 mil)")
    p.add_argument("--repeat", type=int, default=5, help="repetições de cada consulta por rodada (padrão 5)")
    add_common(sub.add_parser("tls", help="M-10: aperto de mão TLS com contexto novo, partilhado e com retomada de sessão"), "tls")
    p = sub.add_parser("replay", help="PF-09: toca uma reunião real no filho ao vivo num banco temporário, com ou sem carga")
    add_common(p, "replay", runs=1, minutes=0.0)
    p.add_argument("--speed", type=float, default=1.0, help="ritmo (1,0 = tempo real; mais rápido distorce latency_ms)")
    p.add_argument("--load", type=float, default=0.0, help="fração da máquina ocupada por um filho de carga (0 = sem carga; 0,85)")
    p.add_argument("--load-class", default="below_normal", choices=("idle", "below_normal", "normal"), help="classe da carga")
    p.add_argument("--names", action="store_true", help="copia o token do Hugging Face para o banco temporário e liga os nomes")
    add_child(sub.add_parser("_child", help=argparse.SUPPRESS))
    return ap


def add_child(p: argparse.ArgumentParser) -> None:
    """Os processos filhos (interno): `threads` (M-01), `load` e `probe` (M-04 e replay)."""
    sub = p.add_subparsers(dest="child", required=True)
    c = sub.add_parser("threads")
    c.add_argument("--npy", required=True)
    c.add_argument("--threads", type=int, required=True)
    c.add_argument("--what", default="diar,whisper")
    c.add_argument("--language", default=None)
    c.add_argument("--db", default=None)
    c.add_argument("--p-cores", dest="p_cores", action="store_true")
    c = sub.add_parser("load")
    c.add_argument("--threads", type=int, default=12)
    c.add_argument("--fraction", type=float, default=1.0)
    c = sub.add_parser("probe")
    c.add_argument("--npy", required=True)
    c.add_argument("--threads", type=int, default=2)
    c.add_argument("--repeat", type=int, default=10)
    c.add_argument("--seconds", type=float, default=0.0)
    c.add_argument("--language", default="pt")
    c.add_argument("--db", default=None)
    c.add_argument("--p-cores", dest="p_cores", action="store_true")


def run_child(args: argparse.Namespace) -> None:
    import faulthandler

    from perf import m_priority, m_threads

    watchdog = os.environ.get("PROBE_PERF_WATCHDOG_S", "").strip()      # depuração: as pilhas de todas as threads no stderr
    if watchdog:
        faulthandler.dump_traceback_later(float(watchdog), repeat=True)
    if args.child == "threads":
        if args.p_cores:
            from perf.common import pin_p_cores

            pin_p_cores()
        m_threads.child(args)
    elif args.child == "load":
        m_priority.load_child(args)
    else:
        m_priority.probe_child(args)


def params_of(args: argparse.Namespace) -> dict[str, Any]:
    from perf.common import safe_path

    params = {k: v for k, v in vars(args).items() if k != "cmd"}
    for key in ("audio", "db", "out"):
        if params.get(key):
            params[key] = safe_path(params[key])
    return params


def run_probe(args: argparse.Namespace) -> None:
    from perf import audio, common

    db_dir = common.real_db_dir(args.db)
    store = common.open_real(db_dir)
    try:
        if store is not None and args.quiet_check:
            reason = common.busy_reason(store)
            if reason:
                raise SystemExit(f"não medindo agora: {reason} (--no-quiet-check ignora)")
        mask = common.pin_p_cores() if args.p_cores else 0
        silence_rms = float(store.get_setting("capture.silence_rms")) if store is not None else 0.004
        language = args.language or common.meeting_language(store, args.meeting)
        config = common.job_config(store, args.torch_threads)
        if args.cmd in TORCH_PROBES or args.cmd == "pack":
            common.prepare_environment(config)
        report = common.Report(args.cmd, params_of(args) | {"pinned_mask": f"{mask:#x}"},
                               Path(args.out) if args.out else common.default_out_dir())
        source = audio.resolve(args, store) if args.cmd in AUDIO_PROBES else None
        dispatch(args, report, store, db_dir, source, config, language, silence_rms)
    finally:
        if store is not None:
            store.close()


def dispatch(args: argparse.Namespace, report: Any, store: Any, db_dir: Path | None, source: Any, config: Any, language: str,
             silence_rms: float) -> None:
    from perf import common, m_batched, m_diar, m_echo, m_live, m_priority, m_sqlite, m_threads, m_tls, m_weights, replay

    token = common.read_token(store)
    match args.cmd:
        case "threads":
            m_threads.run(args, report, source, db_dir, language)
        case "diar":
            m_diar.run(args, report, token, language, source)
        case "batched":
            m_batched.run(args, report, config, language, source)
        case "priority":
            m_priority.run(args, report, source, db_dir, language, silence_rms)
        case "echo":
            m_echo.run(args, report, store)
        case "pack":
            m_live.run_pack(args, report, config, language, source, silence_rms)
        case "embed":
            m_live.run_embed(args, report, token, source, silence_rms)
        case "weights":
            m_weights.run(args, report, token, source, silence_rms)
        case "sqlite":
            m_sqlite.run(args, report)
        case "tls":
            m_tls.run(args, report, store)
        case "replay":
            replay.run(args, report, store)


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            s.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args()
    warnings.filterwarnings("ignore", category=UserWarning)      # o aviso do torchcodec sem FFmpeg (D-025) e os do pyannote
    threads = getattr(args, "torch_threads", None) or getattr(args, "threads", None)
    if isinstance(threads, int):                   # antes do primeiro import de numpy e torch (o OpenMP lê ao carregar)
        for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS"):
            os.environ.setdefault(var, str(max(1, threads)))
    if args.cmd == "_child":
        run_child(args)
        return 0
    run_probe(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
