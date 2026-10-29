"""F5.6 (R-01, R-02): os arquivos de áudio anexados nas conexões deste processo (`Core.files`). O `VACUUM` inteiro de um
arquivo segura a escrita dele até o fim, e a conexão que o tem anexado trava no `BEGIN IMMEDIATE` (que pede a escrita de
todos os anexos) e falha em 5 s ("database is locked") em qualquer ação da tela. Por isso o `VACUUM` inteiro só roda num
arquivo que nenhuma conexão do app anexou (`exclusive`), e anexar o arquivo durante o `VACUUM` o interrompe antes (o SQLite
desfaz a transação dele; o arquivo fica pendente para a próxima abertura ociosa)."""
from __future__ import annotations

import threading
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager

from ..log import get_logger

log = get_logger("store.attached")


class AttachedFiles:
    """Quantas conexões deste processo têm cada arquivo anexado e quem segura um `VACUUM` inteiro dele."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._users: Counter[str] = Counter()
        self._exclusive: dict[str, Callable[[], None]] = {}

    def attaching(self, file_name: str) -> None:
        """Antes do `ATTACH`: conta a conexão e interrompe o `VACUUM` inteiro em curso do arquivo."""
        with self._lock:
            self._users[file_name] += 1
            stop = self._exclusive.get(file_name)
        if stop is not None:
            log.info("%s anexado durante o VACUUM inteiro: o VACUUM para e fica pendente", file_name)
            stop()

    def detached(self, files: Iterable[str]) -> None:
        with self._lock:
            for file_name in files:
                self._users[file_name] -= 1
                if self._users[file_name] <= 0:
                    del self._users[file_name]

    def users(self, file_name: str) -> int:
        with self._lock:
            return self._users.get(file_name, 0)

    @contextmanager
    def exclusive(self, file_name: str, interrupt: Callable[[], None]) -> Iterator[bool]:
        """True quando nenhuma conexão deste processo tem o arquivo anexado; aí, até sair, anexá-lo chama `interrupt`."""
        with self._lock:
            alone = not self._users.get(file_name) and file_name not in self._exclusive
            if alone:
                self._exclusive[file_name] = interrupt
        try:
            yield alone
        finally:
            if alone:
                with self._lock:
                    self._exclusive.pop(file_name, None)

    def clear(self) -> None:
        with self._lock:
            self._users.clear()


__all__ = ["AttachedFiles"]
