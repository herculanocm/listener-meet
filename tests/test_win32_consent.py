"""F5.7 (L1): o indicador de microfone do Windows (`platform/win32/consent.py`). A leitura das entradas, os ids opacos, a
entrada para o executável e o modo só pelo registro com um registro falso (em qualquer sistema); o `Win32MicWatch` real só
arma, espera curto e cancela (no Windows); o mapeamento da espera com a espera de mentira."""
from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Any

import pytest

from listener.platform.base import AppAudio, AudioActivity
from listener.platform.win32 import consent

win32_only = pytest.mark.skipif(sys.platform != "win32", reason="o registro só existe no Windows")
BASE = consent.CONSENT_MIC
NP = rf"{BASE}\NonPackaged"
CHROME = "C:#Program Files#Google#Chrome#Application#chrome.exe"
ZOOM = "C:#Users#Fulano#AppData#Roaming#Zoom#bin#Zoom.exe"
OWN = "C:#Users#Fulano#AppData#Local#Programs#Python#Python312#python.exe"


class FakeRegistry:
    """Chaves por caminho: as subchaves e os dois tempos; o que não está aqui não abre (OSError)."""

    def __init__(self, keys: dict[str, list[str]], times: dict[str, tuple[int, int]]) -> None:
        self.keys, self.time_of = keys, times

    def subkeys(self, path: str) -> list[str]:
        if path not in self.keys:
            raise FileNotFoundError(2, "chave não existe")
        return list(self.keys[path])

    def times(self, path: str) -> tuple[int, int]:
        if path not in self.time_of:
            raise FileNotFoundError(2, "entrada sumiu")
        return self.time_of[path]


def _registry() -> FakeRegistry:
    """O Teams novo e o Chrome em uso, a Câmera parada, o Zoom nunca usado, o próprio Python em uso e uma entrada que some
    entre a lista e a leitura."""
    keys = {BASE: ["MSTeams_8wekyb3d8bbwe", "Microsoft.WindowsCamera_8wekyb3d8bbwe", "NonPackaged", "Sumiu_123"],
            NP: [CHROME, ZOOM, OWN]}
    times = {rf"{BASE}\MSTeams_8wekyb3d8bbwe": (5, 0), rf"{BASE}\Microsoft.WindowsCamera_8wekyb3d8bbwe": (5, 6),
             rf"{NP}\{CHROME}": (7, 0), rf"{NP}\{ZOOM}": (0, 0), rf"{NP}\{OWN}": (9, 0)}
    return FakeRegistry(keys, times)


def test_the_entries_are_read_packaged_and_non_packaged():
    entries = consent.mic_entries(_registry())
    assert entries is not None
    assert [(e.name, e.packaged, e.in_use) for e in entries] == [
        ("MSTeams_8wekyb3d8bbwe", True, True), ("Microsoft.WindowsCamera_8wekyb3d8bbwe", True, False),
        (CHROME, False, True), (ZOOM, False, False), (OWN, False, True)]


def test_without_the_key_there_is_no_reading_and_the_log_has_no_path(caplog):
    with caplog.at_level(logging.DEBUG, logger="listener"):
        assert consent.mic_entries(FakeRegistry({}, {})) is None
    assert "ConsentStore" not in caplog.text and "Fulano" not in caplog.text
    only_packaged = FakeRegistry({BASE: ["MSTeams_8wekyb3d8bbwe", "NonPackaged"]}, {rf"{BASE}\MSTeams_8wekyb3d8bbwe": (5, 0)})
    assert [e.name for e in consent.mic_entries(only_packaged) or []] == ["MSTeams_8wekyb3d8bbwe"]


def test_the_users_are_opaque_ids_without_the_own_entry():
    entries = consent.mic_entries(_registry()) or []
    own = OWN.replace("#", "\\")
    ids = consent.user_ids(entries, own.replace("\\", "#"))
    assert len(ids) == 2 and all(len(i) == 16 and int(i, 16) >= 0 for i in ids)
    assert not any(part in i for i in ids for part in ("Fulano", "chrome", "MSTeams"))
    assert ids == consent.user_ids(entries, OWN.upper())                     # estável; o próprio, sem caixa
    assert len(consent.user_ids(entries, "outro")) == 3                       # sem ignorar: o Python entra


def test_the_own_entries_are_the_base_interpreter_path_and_filter_by_default(monkeypatch):
    """Sob o venv, o `python.exe` base (filho do lançador), cru e resolvido, em minúsculas; sem `own`, `user_ids` e o modo
    só pelo registro tiram o próprio app por eles. Os nomes ficam guardados no processo (F5.8): o teste limpa antes e
    depois."""
    consent.own_entries.cache_clear()
    try:
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.setattr(sys, "_base_executable", OWN.replace("#", "\\"), raising=False)
        assert consent.own_entries() == {OWN.lower()}
        entries = consent.mic_entries(_registry()) or []
        assert consent.user_ids(entries) == consent.user_ids(entries, OWN) and len(consent.user_ids(entries)) == 2
        assert consent.degraded_activity(entries) == consent.degraded_activity(entries, OWN)
    finally:
        consent.own_entries.cache_clear()


def test_an_entry_becomes_its_executable_file_name():
    def entry(name: str, packaged: bool) -> consent.ConsentEntry:
        return consent.ConsentEntry(name, packaged, 1, 0)

    assert consent.entry_exe(entry("MSTeams_8wekyb3d8bbwe", True)) == "ms-teams.exe"
    assert consent.entry_exe(entry("mSteams_8WEKYB3D8BBWE", True)) == "ms-teams.exe"
    assert consent.entry_exe(entry("Microsoft.WindowsCamera_8wekyb3d8bbwe", True)) == ""
    assert consent.entry_exe(entry(ZOOM, False)) == "zoom.exe" and consent.entry_exe(entry(CHROME, False)) == "chrome.exe"
    assert consent.entry_exe(entry("C:#Programas#ferramenta", False)) == ""


def test_the_degraded_activity_is_one_app_per_executable_in_use():
    entries = consent.mic_entries(_registry()) or []
    entries.append(consent.ConsentEntry(CHROME.replace("Program Files", "Arquivos"), False, 3, 0))   # outro Chrome em uso
    got = consent.degraded_activity(entries, OWN)
    assert got == AudioActivity((AppAudio(0, "chrome.exe", True, 0.0), AppAudio(0, "ms-teams.exe", True, 0.0)), degraded=True)
    assert consent.degraded_activity([], OWN) == AudioActivity((), degraded=True)


# ---------------------------------------------------------------- o aviso do registro
class FakeKey:
    def __init__(self) -> None:
        self.handle, self.closed = 42, False

    def Close(self) -> None:  # noqa: N802 (nome do winreg)
        self.closed = True


def _watch(results: list[int], arms: list[bool]) -> tuple[consent.Win32MicWatch, list[int]]:
    """Uma espera de mentira: `_wait_any` devolve os códigos dados, `_arm` os resultados dados; anota os tempos-limite."""
    watch = consent.Win32MicWatch(FakeKey(), 1, 2)
    asked: list[int] = []

    def wait_any(timeout_ms: int) -> int:
        asked.append(timeout_ms)
        return results.pop(0)

    watch._wait_any = wait_any                                              # type: ignore[method-assign]
    watch._arm = lambda: arms.pop(0)                                        # type: ignore[method-assign]
    return watch, asked


def test_the_wait_maps_the_kernel_answers():
    """O registro mudou: True e rearma; o tempo-limite e o `cancel` (o evento de parada vem primeiro): False; a espera que
    falha ou o rearme que falha levantam (quem lê passa a consultar no tempo-limite)."""
    arms = [True, False]
    watch, asked = _watch([consent.WAIT_OBJECT_0 + 1, consent.WAIT_TIMEOUT, consent.WAIT_OBJECT_0, 0xFFFFFFFF,
                           consent.WAIT_OBJECT_0 + 1], arms)
    assert watch.wait(2.5) is True and arms == [False]
    assert watch.wait(-1) is False and watch.wait(1e12) is False
    assert asked == [2500, 0, consent.MAX_WAIT_MS]
    with pytest.raises(OSError, match="WaitForMultipleObjects"):
        watch.wait(1)
    with pytest.raises(OSError, match="não rearmou"):
        watch.wait(1)
    watch._closed = True                                                    # fechado: nem espera
    assert watch.wait(1) is False and len(asked) == 5


@win32_only
def test_the_real_watch_arms_waits_short_and_wakes_on_cancel():
    """Só arma, espera curto e cancela: que avise num uso real é [chute] (medido: 0 avisos em 20 min parados)."""
    watch = consent.Win32MicWatch.open()
    assert watch is not None
    assert isinstance(watch.wait(0.05), bool)
    got: list[bool] = []
    t = threading.Thread(target=lambda: got.append(watch.wait(10)))
    t.start()
    time.sleep(0.05)
    t0 = time.perf_counter()
    watch.cancel()
    t.join(2)
    assert not t.is_alive() and got == [False] and time.perf_counter() - t0 < 2
    t0 = time.perf_counter()
    assert watch.wait(5) is False and time.perf_counter() - t0 < 0.5         # o cancel vale até o fim
    watch.close()
    watch.close()
    assert watch.wait(5) is False


@win32_only
def test_the_platform_reads_the_real_indicator():
    from listener.platform.win32.platform import Win32Platform

    ids: Any = Win32Platform().mic_users()
    assert ids is None or all(len(i) == 16 for i in ids)
    entries = consent.mic_entries()
    assert entries is None or all(isinstance(e.in_use, bool) for e in entries)


@win32_only
def test_the_watch_armed_in_a_thread_that_exits_does_not_fire():
    """M07: sem o `REG_NOTIFY_THREAD_AGNOSTIC`, a saída da thread que armou sinaliza o evento sozinha."""
    box: dict[str, consent.Win32MicWatch | None] = {}
    t = threading.Thread(target=lambda: box.setdefault("w", consent.Win32MicWatch.open()))
    t.start()
    t.join(5)
    watch = box["w"]
    assert watch is not None
    try:
        assert watch.wait(0.3) is False
    finally:
        watch.close()


class SlowKernel:
    """kernel32 de mentira: o `SetEvent` demora e anota se o evento foi fechado no meio (R-10)."""

    def __init__(self) -> None:
        self.order: list[str] = []
        self.entered = threading.Event()

    def SetEvent(self, handle: int) -> int:  # noqa: N802 (nome do Windows)
        self.order.append("set-start")
        self.entered.set()
        time.sleep(0.15)
        self.order.append("set-end")
        return 1

    def CloseHandle(self, handle: int) -> int:  # noqa: N802
        self.order.append(f"close {handle}")
        return 1


def test_cancel_and_close_never_cross(monkeypatch):
    """R-10: o `close` de outra thread espera o `cancel` terminar; o `SetEvent` nunca pega um evento já fechado."""
    kernel = SlowKernel()
    monkeypatch.setattr(consent, "_dlls", lambda: (kernel, None))
    watch = consent.Win32MicWatch(FakeKey(), 1, 2)
    t = threading.Thread(target=watch.cancel)
    t.start()
    kernel.entered.wait(2)
    watch.close()
    t.join(2)
    assert kernel.order == ["set-start", "set-end", "close 1", "close 2"]
    watch.cancel()
    assert kernel.order[-1] == "close 2"                                    # fechado: o cancel não toca o evento


def test_the_entry_repr_has_no_name():
    """R-12: o caminho (com a pasta do usuário) não aparece no `repr` de uma entrada."""
    entry = consent.ConsentEntry(ZOOM, False, 1, 0)
    assert "Fulano" not in repr(entry) and "Zoom" not in repr(entry) and "start=1" in repr(entry)
