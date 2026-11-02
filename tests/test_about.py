"""F5.12 L1 (U-121; CP15, D-079): os dados públicos do projeto em `listener/about.py`, sem Qt. O e-mail de doação, o autor e o
repositório moram num lugar só (teste estático em todo `listener/`); a licença de cada biblioteca dos créditos sai da
metadata instalada, com a reserva quando ela falta ou traz o texto inteiro; a cópia para suporte não leva caminho."""
from __future__ import annotations

import sys
from email.message import Message
from importlib import metadata
from pathlib import Path

import pytest

from listener import about

ROOT = Path(__file__).resolve().parent.parent / "listener"
HEAVY = ("torch", "pyannote", "faster_whisper", "ctranslate2", "onnxruntime", "tokenizers", "openai", "anthropic", "google.genai")


def test_the_email_the_author_and_the_repository_live_only_in_about_py():
    """CP15 (pergunta 1, D-079): o dado público do projeto num lugar só; quem fizer um fork troca ali."""
    assert about.DONATION_EMAIL == "herculanocm@outlook.com" and about.AUTHOR == "Herculano"
    assert about.REPOSITORY == "https://github.com/herculanocm/listener-meet" and about.LICENSE == "MIT"
    found = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.suffix not in (".py", ".svg", ".toml", ".json", ".md") or path.name == "about.py":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        found += [f"{path.relative_to(ROOT).as_posix()}: {what}" for what in (about.DONATION_EMAIL, "herculanocm", about.AUTHOR)
                  if what in text]
    assert found == []
    assert (ROOT / "about.py").read_text(encoding="utf-8").count(about.DONATION_EMAIL) == 1


def test_the_suggested_values_and_the_paypal_page():
    assert (about.PIX_AMOUNT, about.PAYPAL_AMOUNT) == ("R$ 0,99", "US$ 0,20")
    assert about.PAYPAL_URL.startswith("https://www.paypal.com/") and about.DONATION_EMAIL not in about.PAYPAL_URL   # sem dado na URL
    assert __import__("listener").__version__ == about.VERSION and about.APP_NAME == "listener-meet"


def _meta(**fields: str) -> Message:
    msg = Message()
    for key, value in fields.items():
        msg[key.replace("_", "-")] = value
    return msg


@pytest.mark.parametrize(("fields", "expected"), [
    ({"License_Expression": "MIT", "License": "Apache-2.0"}, "MIT"),                       # a expressão SPDX primeiro
    ({"License": "MIT License"}, "MIT"),                                                    # sem o "License" do fim
    ({"License": "Apache-2.0 license"}, "Apache-2.0"),
    ({"License": "Copyright (c) 2001 Enthought\nAll rights reserved.\n" + "x" * 200}, "reserva"),   # o texto inteiro
    ({"License": "y" * 65}, "reserva"),
    ({}, "reserva"),                                                                        # sem os dois campos
])
def test_the_license_comes_from_the_installed_metadata_with_a_fallback(monkeypatch, fields, expected):
    monkeypatch.setattr(metadata, "metadata", lambda _dist: _meta(**fields))
    assert about.license_of(about.Credit("Biblioteca", "biblioteca", "reserva")) == expected


def test_a_missing_distribution_or_one_without_metadata_uses_the_fallback(monkeypatch):
    def missing(dist: str) -> Message:
        raise metadata.PackageNotFoundError(dist)

    monkeypatch.setattr(metadata, "metadata", missing)
    assert about.license_of(about.Credit("Outra", "nao-instalada", "LGPL-3.0")) == "LGPL-3.0"
    assert about.license_of(about.Credit("SQLite", "", "domínio público")) == "domínio público"


def test_the_credits_list_every_main_library_without_importing_it():
    before = {name for name in HEAVY if name in sys.modules}
    shown = about.credits()
    assert [name for name, _lic in shown] == [c.name for c in about.CREDITS]
    assert all(lic and len(lic) <= about.LICENSE_MAX for _name, lic in shown)
    names = {c.name for c in about.CREDITS}
    assert {"Qt for Python (PySide6)", "SQLite", "faster-whisper", "pyannote.audio", "NumPy"} <= names
    assert {name for name in HEAVY if name in sys.modules} == before                       # metadata não importa a biblioteca
    assert dict(shown)["SciPy"] == "BSD-3-Clause"                       # a metadata traz o texto inteiro: a reserva


def test_the_support_info_has_versions_and_the_system_but_no_path():
    text = about.support_info("3.12.10", "6.12.0", "Windows 11 Version 25H2")
    assert text == f"listener-meet {about.VERSION}\nPython 3.12.10\nQt 6.12.0\nSistema: Windows 11 Version 25H2"
    assert "\\" not in text and "/" not in text
