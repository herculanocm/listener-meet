"""Idioma (F5.3 L1, U-044, A-02, D-060): o único lugar das conversões entre os três códigos do app. `app.language` e a
saída da LLM usam `pt-BR` ou `en`; `meetings.language` guarda o código do Whisper (`pt` ou `en`, como a detecção da D-013
gravava); o .docx marca a revisão ortográfica em `pt-BR` ou `en-US`. Sem detecção: o idioma da reunião é escolhido no
warm-up (o padrão das Configurações quando vazio) e só o usuário o troca, reprocessando (A-01, A-05). Qualquer código
lido (`pt`, `pt-BR`, `en`, `en-US`, maiúsculo ou com `_`) cai num dos dois; o que não é inglês vale português, o padrão
(outros idiomas estão fora de escopo)."""
from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ..log import get_logger

if TYPE_CHECKING:
    from ..store import Store

log = get_logger("config.language")

SETTING = "app.language"
LEGACY_SETTING = "llm.output_language"           # F3: só a saída da LLM; lido uma vez na abertura (A-03)
PT, EN = "pt-BR", "en"
DEFAULT = PT
LANGUAGES: tuple[str, ...] = (PT, EN)
LABELS: dict[str, str] = {PT: "Português (pt-BR)", EN: "Inglês (en)"}
_WHISPER: dict[str, str] = {PT: "pt", EN: "en"}
_DOCX: dict[str, str] = {PT: "pt-BR", EN: "en-US"}


def _primary(code: str | None) -> str:
    """A subetiqueta principal, minúscula: "en-US" -> "en", "pt_BR" -> "pt"."""
    return str(code or "").strip().replace("_", "-").split("-")[0].lower()


def output_code(code: str | None) -> str:
    """O código do app e da saída da LLM: `en` para qualquer inglês, `pt-BR` para o resto (vazio inclusive)."""
    return EN if _primary(code) == "en" else PT


def whisper_code(code: str | None) -> str:
    """O código do Whisper e de `meetings.language`: `pt` ou `en`."""
    return _WHISPER[output_code(code)]


def docx_code(code: str | None) -> str:
    """O `w:lang` do .docx (a revisão ortográfica do Word, A-07): `pt-BR` ou `en-US`."""
    return _DOCX[output_code(code)]


def label(code: str | None) -> str:
    """"Português (pt-BR)" ou "Inglês (en)": o texto dos combos e da troca na reunião."""
    return LABELS[output_code(code)]


def other(code: str | None) -> str:
    """O outro idioma (a troca da reunião aberta oferece só ele)."""
    return EN if output_code(code) == PT else PT


def default_language(store: Store) -> str:
    """`app.language`, validado pela tabela (inválido no banco vira o padrão com aviso no log)."""
    from .defaults import SPECS

    return output_code(str(store.get(SPECS[SETTING])))


def meeting_language(store: Store, meeting: Mapping[str, Any] | None) -> str:
    """O idioma de uma reunião no código do app: o gravado nela ou, vazio (reunião anterior à F5.3), o padrão (A-01)."""
    code = (meeting or {}).get("language")
    return output_code(str(code)) if code else default_language(store)


def adopt_legacy(store: Store) -> bool:
    """A-03, na abertura do app: quem usava `llm.output_language = en` passa a ter `app.language = en`, uma vez (só quando
    a chave nova não existe). A linha antiga fica no banco (CP3); ela não é lida em mais nenhum lugar."""
    if store.get_setting(SETTING, "") or _primary(store.get_setting(LEGACY_SETTING, "")) != "en":
        return False
    store.set_setting(SETTING, EN)
    log.info("idioma padrão: en, adotado do antigo %s (F5.3 A-03)", LEGACY_SETTING)
    return True


__all__ = ["DEFAULT", "EN", "LABELS", "LANGUAGES", "PT", "SETTING", "adopt_legacy", "default_language", "docx_code", "label",
           "meeting_language", "other", "output_code", "whisper_code"]
