"""F5.12 (U-121 a U-128; CP15, D-079): os dados públicos do projeto num lugar só, sem Qt e sem rede: o nome, a versão, a
frase, o autor, o repositório, a licença, o e-mail de doação (a chave Pix e a conta do PayPal), os valores sugeridos e o
endereço do PayPal. Quem fizer um fork troca aqui (pergunta 1); um teste estático confere que o e-mail, o autor e o
repositório não aparecem em mais nenhum arquivo de `listener/`. O e-mail nunca vai ao log (D-053).

Os créditos são as bibliotecas abertas principais, com a licença lida da metadata instalada (`importlib.metadata`: sem
importar a biblioteca e sem rede) e um valor de reserva quando a metadata falta (a biblioteca de outro sistema, o
executável da F5.8) ou traz o texto inteiro da licença no lugar do nome dela."""
from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import metadata

from . import __version__

APP_NAME = "listener-meet"
VERSION = __version__
TAGLINE = "Grave qualquer reunião, saiba quem falou e ache qualquer decisão, tudo no seu computador."
AUTHOR = "Herculano"
REPOSITORY = "https://github.com/herculanocm/listener-meet"
LICENSE = "MIT"
DONATION_EMAIL = "herculanocm@outlook.com"             # a chave Pix (U-124) e a conta do PayPal (U-125)
PIX_AMOUNT = "R$ 0,99"
PAYPAL_AMOUNT = "US$ 0,20"
PAYPAL_URL = "https://www.paypal.com/myaccount/transfer/homepage/pay"     # [chute] a página de enviar dinheiro do PayPal
LICENSE_MAX = 64                     # acima disto a metadata trouxe o texto inteiro da licença: vale a de reserva
_SUFFIX = re.compile(r"\s+licen[cs]e$", re.IGNORECASE)          # "MIT License" e "Apache-2.0 license" viram o nome só


@dataclass(frozen=True, slots=True)
class Credit:
    """Uma biblioteca dos créditos: o nome na tela, a distribuição instalada ("" sem metadata, como o SQLite, que vem com o
    Python) e a licença de reserva."""
    name: str
    dist: str
    fallback: str


# a reserva é a da metadata instalada em 02/11/2026 (Windows, o venv do projeto); a do pynput e a do pyannote.audio, que não
# a trazem aqui, são as dos repositórios deles
CREDITS: tuple[Credit, ...] = (
    Credit("Qt for Python (PySide6)", "PySide6", "LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only"),
    Credit("SQLite", "", "domínio público"),
    Credit("NumPy", "numpy", "BSD-3-Clause"),
    Credit("SciPy", "scipy", "BSD-3-Clause"),
    Credit("python-soundfile", "soundfile", "BSD-3-Clause"),
    Credit("python-sounddevice", "sounddevice", "MIT"),
    Credit("proc-tap", "proc-tap", "MIT"),
    Credit("faster-whisper", "faster-whisper", "MIT"),
    Credit("CTranslate2", "ctranslate2", "MIT"),
    Credit("pyannote.audio", "pyannote.audio", "MIT"),
    Credit("PyTorch", "torch", "BSD-3-Clause"),
    Credit("ONNX Runtime", "onnxruntime", "MIT"),
    Credit("Tokenizers", "tokenizers", "Apache-2.0"),
    Credit("Hugging Face Hub", "huggingface-hub", "Apache-2.0"),
    Credit("Google Gen AI", "google-genai", "Apache-2.0"),
    Credit("OpenAI", "openai", "Apache-2.0"),
    Credit("Anthropic", "anthropic", "Apache-2.0"),
    Credit("truststore", "truststore", "MIT"),
    Credit("python-docx", "python-docx", "MIT"),
    Credit("pycaw", "pycaw", "MIT"),
    Credit("comtypes", "comtypes", "MIT"),
    Credit("PyAudioWPatch", "PyAudioWPatch", "Apache-2.0"),
    Credit("pynput", "pynput", "LGPL-3.0"),
)


def _short(text: str) -> str:
    """O nome da licença numa linha, sem o "License" do fim; vazio quando é o texto inteiro (ou não há)."""
    line = " ".join((text or "").split())
    if not line or len(line) > LICENSE_MAX or "\n" in (text or "").strip():
        return ""
    return _SUFFIX.sub("", line)


def license_of(credit: Credit) -> str:
    """A licença de uma biblioteca: a expressão SPDX da metadata (`License-Expression`), o campo `License` quando ele é só
    o nome, ou a de reserva (sem a distribuição instalada, sem os dois campos ou com o texto inteiro)."""
    if not credit.dist:
        return credit.fallback
    try:
        meta = metadata.metadata(credit.dist)
    except metadata.PackageNotFoundError:
        return credit.fallback
    for field in ("License-Expression", "License"):
        found = _short(str(meta.get(field) or ""))
        if found:
            return found
    return credit.fallback


def credits() -> list[tuple[str, str]]:
    """(nome, licença) de cada biblioteca dos créditos, na ordem de `CREDITS`."""
    return [(c.name, license_of(c)) for c in CREDITS]


def support_info(python: str, qt: str, system: str) -> str:
    """O texto de "Copiar informações para suporte": as versões e o sistema, sem caminho (a pasta do banco e o arquivo de
    log têm o nome do usuário)."""
    return f"{APP_NAME} {VERSION}\nPython {python}\nQt {qt}\nSistema: {system}"


__all__ = ["APP_NAME", "AUTHOR", "CREDITS", "DONATION_EMAIL", "LICENSE", "LICENSE_MAX", "PAYPAL_AMOUNT", "PAYPAL_URL",
           "PIX_AMOUNT", "REPOSITORY", "TAGLINE", "VERSION", "Credit", "credits", "license_of", "support_info"]
