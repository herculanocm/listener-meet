"""M-10 `tls` (PF-25): o aperto de mão TLS com `generativelanguage.googleapis.com` (só `connect` mais handshake com `ssl`, sem
chamada à API, sem chave). Com `llm.system_certs` ligado no banco real, o `truststore` é injetado como `llm/registry.py::
prepare_ssl` faz (`inject_into_ssl`, que troca o `ssl.SSLContext` do processo pelo do truststore: é esse o contexto que o
httpx dos SDKs cria); daí `ssl.create_default_context()` é o contexto "de hoje". Três condições, 5 conexões cada: um
contexto novo por conexão (hoje); um contexto só reaproveitado (o que um `NetworkConfig` partilharia); e o mesmo contexto
com a `session` da conexão anterior passada ao `wrap_socket` (a retomada TLS de verdade, `session_reused`). Saída: ms da
criação do contexto, do TCP e do handshake por conexão. Sem rede, registra o erro e segue."""
from __future__ import annotations

import socket
import ssl
import time
from typing import Any

from .common import Report, interleaved, stats

HOST, PORT = "generativelanguage.googleapis.com", 443
CONNECTIONS = 5
TIMEOUT_S = 15.0


def inject_truststore(system_certs: bool) -> dict[str, Any]:
    from listener.llm.registry import NetworkConfig, prepare_ssl

    injected = prepare_ssl(NetworkConfig(system_certs=system_certs))
    return {"system_certs": system_certs, "truststore_injected": injected, "ssl_context_class": f"{ssl.SSLContext.__module__}.{ssl.SSLContext.__name__}"}


def connect(ctx: ssl.SSLContext, session: Any = None, fetch_ticket: bool = False) -> dict[str, Any]:
    """Uma conexão: TCP, depois o handshake no `wrap_socket`; devolve os tempos, a versão, o cifrador e a sessão para reúso.
    No TLS 1.3 o bilhete de sessão chega depois do handshake, só ao ler do servidor: com `fetch_ticket`, um `HEAD /` (sem API,
    sem chave) lê a resposta para o bilhete entrar na sessão (`head_ms`, fora do tempo do handshake)."""
    t0 = time.perf_counter()
    sock = socket.create_connection((HOST, PORT), timeout=TIMEOUT_S)
    t_tcp = time.perf_counter()
    try:
        ss = ctx.wrap_socket(sock, server_hostname=HOST, session=session)
        t_hs = time.perf_counter()
        try:
            info = {"tcp_ms": round((t_tcp - t0) * 1000, 1), "handshake_ms": round((t_hs - t_tcp) * 1000, 1),
                    "version": ss.version(), "cipher": (ss.cipher() or ("?",))[0], "session_reused": bool(ss.session_reused)}
            info["head_ms"] = _fetch_ticket(ss) if fetch_ticket else None       # depois: o servidor fecha a conexão no fim
            info["session"] = ss.session
            info["has_ticket"] = bool(getattr(ss.session, "has_ticket", False))  # sem bilhete do servidor não há retomada
        finally:
            ss.close()
    finally:
        sock.close()
    return info


def _fetch_ticket(ss: ssl.SSLSocket) -> float:
    t0 = time.perf_counter()
    ss.sendall(f"HEAD / HTTP/1.1\r\nHost: {HOST}\r\nConnection: close\r\n\r\n".encode("ascii"))
    while ss.recv(4096):
        pass
    return round((time.perf_counter() - t0) * 1000, 1)


def condition(label: str, new_context: bool, reuse_session: bool) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    ctx: ssl.SSLContext | None = None
    session: Any = None
    for i in range(CONNECTIONS):
        t0 = time.perf_counter()
        if ctx is None or new_context:
            ctx = ssl.create_default_context()
        ctx_ms = (time.perf_counter() - t0) * 1000
        try:
            info = connect(ctx, session if reuse_session else None, fetch_ticket=reuse_session)
        except (OSError, ssl.SSLError) as e:
            rows.append({"i": i + 1, "error": f"{type(e).__name__}: {e}"})
            continue
        session = info.pop("session")
        rows.append({"i": i + 1, "ctx_ms": round(ctx_ms, 1), **info, "total_ms": round(ctx_ms + info["tcp_ms"] + info["handshake_ms"], 1)})
    ok = [r for r in rows if "error" not in r]
    return {"condition": label, "connections": rows,
            "ctx_ms": stats([r["ctx_ms"] for r in ok], 1), "handshake_ms": stats([r["handshake_ms"] for r in ok], 1),
            "total_ms": stats([r["total_ms"] for r in ok], 1), "reused": sum(1 for r in ok if r["session_reused"]),
            "tickets": sum(1 for r in ok if r.get("has_ticket")), "errors": len(rows) - len(ok)}


def run(args: Any, report: Report, store: Any) -> None:
    from listener.config.defaults import SPECS

    system_certs = bool(store.get(SPECS["llm.system_certs"])) if store is not None else True
    report.add("tls_setup", host=HOST, **inject_truststore(system_certs), openssl=ssl.OPENSSL_VERSION)
    conditions = (("contexto_novo_por_conexao", True, False), ("contexto_partilhado", False, False), ("contexto_partilhado_com_sessao", False, True))
    summary: dict[str, Any] = {}
    for _run_idx, (label, new_ctx, reuse) in interleaved(conditions, args.runs):
        result = condition(label, new_ctx, reuse)
        report.add("tls_condition", **result)
        current = summary.get(label)
        median = result["total_ms"].get("median")
        if median is not None and (current is None or median < current["total_ms_median"]):
            summary[label] = {"total_ms_median": median, "handshake_ms_median": result["handshake_ms"].get("median"),
                              "ctx_ms_median": result["ctx_ms"].get("median"), "reused": result["reused"]}
    report.finish(best=summary, system_certs=system_certs)
