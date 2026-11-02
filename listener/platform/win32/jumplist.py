"""F5.13 (L2, U-131): o Jump List do ícone na barra de tarefas, no grupo "Tarefas" que o Windows desenha, por comtypes (vem
com o pycaw: nenhuma dependência nova). `ICustomDestinationList` (CLSID_DestinationList) com o AppUserModelID do processo
(`SetAppID`); uma `IShellLinkW` (CLSID_ShellLink) por tarefa, com o programa, os argumentos, o ícone do próprio programa e o
título pelo `IPropertyStore` (`PKEY_Title`), numa `IObjectCollection` (CLSID_EnumerableObjectCollection) que o
`AddUserTasks` lê como `IObjectArray`; `BeginList`, `AddUserTasks` e `CommitList`.

O PROPVARIANT `VT_LPWSTR` é montado à mão (o `InitPropVariantFromString` é inline nos cabeçalhos do SDK, não é exportado): o
texto fica com o Python e o `SetValue` copia o valor, então nada de `PropVariantClear` nele. A ordem dos métodos de cada
vtable é a dos cabeçalhos do SDK: `shobjidl_core.h` (ICustomDestinationList), `objectarray.h` (IObjectArray e, depois dos
dois dele, IObjectCollection) e `propsys.h` (IPropertyStore); a `IShellLinkW` é a do comtypes (`comtypes.shelllink`), na
mesma ordem de `shobjidl_core.h`. Qualquer erro: `AbortList` (se a lista começou), uma linha de log com o tipo e o código
(nunca o caminho) e False. COM na thread de quem chama, a da interface (o Qt já o iniciou nela); este módulo só é importado
ali e no Windows."""
from __future__ import annotations

import ctypes
import subprocess
import time
from collections.abc import Sequence
from contextlib import suppress
from ctypes import POINTER, Structure, Union, c_int, c_uint, c_ushort, c_void_p, c_wchar_p
from ctypes.wintypes import DWORD
from typing import Any, ClassVar

import comtypes
from comtypes import CLSCTX_INPROC_SERVER, COMMETHOD, GUID, HRESULT, IUnknown
from comtypes.shelllink import IShellLinkW, ShellLink

from ...log import get_logger
from ..base import TaskbarTask
from . import endpoints

log = get_logger("platform.win32.jumplist")

VT_LPWSTR = 31
PKEY_TITLE = ("{F29F85E0-4FF9-1068-AB91-08002B27B3D9}", 2)          # PKEY_Title (propkey.h)
CLSID_DESTINATION_LIST = GUID("{77F10CF0-3DB5-4966-B520-B7C54FD35ED6}")
CLSID_OBJECT_COLLECTION = GUID("{2D3468C1-36A7-43B6-AC24-D3F02FD9607A}")


class PropertyKey(Structure):
    _fields_ = (("fmtid", GUID), ("pid", DWORD))


class PropValue(Union):
    """O union do PROPVARIANT: 16 bytes no x64 (os pares de ponteiros, como o BLOB), 8 no x86; aqui só o `pwszVal`."""
    _fields_ = (("pwszVal", c_void_p), ("pad", c_void_p * 2))


class PropVariant(Structure):
    _fields_ = (("vt", c_ushort), ("reserved1", c_ushort), ("reserved2", c_ushort), ("reserved3", c_ushort), ("value", PropValue))


class IPropertyStore(IUnknown):
    _iid_ = GUID("{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}")
    _methods_: ClassVar[list[Any]] = [
        COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(DWORD), "cProps")),
        COMMETHOD([], HRESULT, "GetAt", (["in"], DWORD, "iProp"), (["out"], POINTER(PropertyKey), "pkey")),
        COMMETHOD([], HRESULT, "GetValue", (["in"], POINTER(PropertyKey), "key"), (["out"], POINTER(PropVariant), "pv")),
        COMMETHOD([], HRESULT, "SetValue", (["in"], POINTER(PropertyKey), "key"), (["in"], POINTER(PropVariant), "propvar")),
        COMMETHOD([], HRESULT, "Commit"),
    ]


class IObjectArray(IUnknown):
    _iid_ = GUID("{92CA9DCD-5622-4BBA-A805-5E9F541BD8C9}")
    _methods_: ClassVar[list[Any]] = [
        COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(c_uint), "pcObjects")),
        COMMETHOD([], HRESULT, "GetAt", (["in"], c_uint, "uiIndex"), (["in"], POINTER(GUID), "riid"),
                  (["out"], POINTER(c_void_p), "ppv")),
    ]


class IObjectCollection(IObjectArray):
    _iid_ = GUID("{5632B1A4-E38A-400A-928A-D4CD63230295}")
    _methods_: ClassVar[list[Any]] = [
        COMMETHOD([], HRESULT, "AddObject", (["in"], POINTER(IUnknown), "punk")),
        COMMETHOD([], HRESULT, "AddFromArray", (["in"], POINTER(IObjectArray), "poaSource")),
        COMMETHOD([], HRESULT, "RemoveObjectAt", (["in"], c_uint, "uiIndex")),
        COMMETHOD([], HRESULT, "Clear"),
    ]


class ICustomDestinationList(IUnknown):
    _iid_ = GUID("{6332DEBF-87B5-4670-90C0-5E57B408A49E}")
    _methods_: ClassVar[list[Any]] = [
        COMMETHOD([], HRESULT, "SetAppID", (["in"], c_wchar_p, "pszAppID")),
        COMMETHOD([], HRESULT, "BeginList", (["out"], POINTER(c_uint), "pcMinSlots"), (["in"], POINTER(GUID), "riid"),
                  (["out"], POINTER(POINTER(IObjectArray)), "ppv")),
        COMMETHOD([], HRESULT, "AppendCategory", (["in"], c_wchar_p, "pszCategory"), (["in"], POINTER(IObjectArray), "poa")),
        COMMETHOD([], HRESULT, "AppendKnownCategory", (["in"], c_int, "category")),
        COMMETHOD([], HRESULT, "AddUserTasks", (["in"], POINTER(IObjectArray), "poa")),
        COMMETHOD([], HRESULT, "CommitList"),
        COMMETHOD([], HRESULT, "GetRemovedDestinations", (["in"], POINTER(GUID), "riid"), (["out"], POINTER(c_void_p), "ppv")),
        COMMETHOD([], HRESULT, "DeleteList", (["in"], c_wchar_p, "pszAppID")),
        COMMETHOD([], HRESULT, "AbortList"),
    ]


def _create(clsid: Any, interface: Any) -> Any:
    return comtypes.CoCreateInstance(clsid, interface=interface, clsctx=CLSCTX_INPROC_SERVER)


def shell_link(task: TaskbarTask) -> Any:
    """Uma tarefa: o programa, os argumentos, o ícone do próprio programa, a dica e o título (o que o Jump List mostra)."""
    link = _create(ShellLink._reg_clsid_, IShellLinkW)
    link.SetPath(task.program)
    link.SetArguments(subprocess.list2cmdline(task.arguments))
    link.SetIconLocation(task.program, 0)
    link.SetDescription(task.title)
    text = ctypes.create_unicode_buffer(task.title)    # vivo até o `SetValue`, que copia o texto
    value = PropVariant()
    value.vt = VT_LPWSTR
    value.value.pwszVal = ctypes.cast(text, c_void_p).value
    store = link.QueryInterface(IPropertyStore)
    store.SetValue(ctypes.byref(PropertyKey(GUID(PKEY_TITLE[0]), PKEY_TITLE[1])), ctypes.byref(value))
    store.Commit()
    return link


def _code(e: Exception) -> str:
    """O HRESULT do comtypes ou o erro do Windows, em hexadecimal; a mensagem fica de fora (pode citar o caminho)."""
    code = getattr(e, "hresult", None)
    if code is None:
        code = getattr(e, "winerror", None)
    return f"0x{code & 0xFFFFFFFF:08X}" if isinstance(code, int) else "sem código"


def publish(app_id: str, tasks: Sequence[TaskbarTask], commit: bool = True) -> bool:
    """O Jump List do `app_id` com `tasks` no grupo "Tarefas". Sem `commit` (o teste), a lista é montada inteira e
    abandonada (`AbortList`): nada muda na barra de tarefas. False, com uma linha de log, em qualquer erro."""
    t0 = time.perf_counter()
    destinations: Any = None
    began = False
    try:
        endpoints.com_init()                           # R-08: o COM desta thread, uma vez (o Qt offscreen não o inicia)
        destinations = _create(CLSID_DESTINATION_LIST, ICustomDestinationList)
        destinations.SetAppID(app_id)
        _slots, _removed = destinations.BeginList(ctypes.byref(IObjectArray._iid_))
        began = True
        items = _create(CLSID_OBJECT_COLLECTION, IObjectCollection)
        for task in tasks:
            items.AddObject(shell_link(task))
        destinations.AddUserTasks(items.QueryInterface(IObjectArray))
        if commit:
            destinations.CommitList()
        else:
            destinations.AbortList()
    except Exception as e:
        if began:
            with suppress(Exception):
                destinations.AbortList()
        log.warning("Jump List não atualizado (%s %s); o app segue sem ele", type(e).__name__, _code(e))
        return False
    log.info("Jump List %s: %d tarefa(s) em %.0f ms", "atualizado" if commit else "montado e abandonado", len(tasks),
             (time.perf_counter() - t0) * 1000)
    return True
