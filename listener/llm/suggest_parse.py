"""F5.20 (C7): o leitor incremental da resposta de uma sugestão ao vivo, puro. A resposta é um JSON com esquema
(`{"options": [...], "thread_summary": "..."}`) que chega em pedaços pelo streaming; cada objeto de `options` é entregue assim
que fecha, e o cartão mostra a primeira opção antes de a resposta terminar (o `thread_summary` vem da validação do texto
inteiro, no fim). Uma máquina de estados por caractere, numa passada, guardando só o fragmento do objeto corrente: a chave
`"options"` no primeiro nível, os dois-pontos e o `[`; dentro da lista, cada `{` do nível dela abre um objeto e o `}` que o
fecha (as chaves e os colchetes dentro das strings não contam, nem o `\\"`) o entrega por `json.loads`; o objeto ilegível conta
em `dropped`; o `]` da lista marca `done`. Cortar o texto em qualquer posição não muda o que sai."""
from __future__ import annotations

import json
from typing import Any

OPTIONS_KEY = "options"


class OptionsReader:
    def __init__(self) -> None:
        self._parts: list[str] = []
        self.dropped = 0                          # objetos com JSON inválido, descartados
        self.done = False                         # o `]` de `options` já passou
        self._depth = 0                           # a profundidade de {} e [] fora das strings
        self._in_str = False
        self._esc = False
        self._key: list[str] | None = None        # a string do primeiro nível em curso (a chave candidata)
        self._last = ""                           # a última string completa do primeiro nível
        self._expect = False                      # depois de `"options":`, esperando o `[`
        self._array = 0                           # a profundidade dentro da lista `options` (0 = fora dela)
        self._obj: list[str] | None = None        # o fragmento do objeto corrente

    @property
    def text(self) -> str:
        """Tudo o que chegou."""
        return "".join(self._parts)

    def feed(self, piece: str) -> list[dict[str, Any]]:
        """Os objetos de `options` que fecharam neste pedaço, em ordem."""
        self._parts.append(piece)
        out: list[dict[str, Any]] = []
        if self.done:
            return out
        for ch in piece:
            if self._obj is not None:
                self._obj.append(ch)
            if self._in_str:
                self._string_char(ch)
                continue
            match ch:
                case '"':
                    self._in_str = True
                    if self._depth == 1 and self._obj is None and not self._array:
                        self._key = []
                case ":":
                    if self._depth == 1 and not self._array and self._last == OPTIONS_KEY:
                        self._expect = True
                case "[":
                    self._depth += 1
                    if self._expect and self._depth == 2:
                        self._array, self._expect = self._depth, False
                case "{":
                    if self._array and self._depth == self._array and self._obj is None:
                        self._obj = ["{"]
                    self._depth += 1
                case "}":
                    self._depth -= 1
                    if self._obj is not None and self._depth == self._array:
                        self._finish(out)
                case "]":
                    self._depth -= 1
                    if self._array and self._depth == self._array - 1:
                        self.done = True
                        break
                case ",":
                    if self._depth == 1:
                        self._last, self._expect = "", False
                case _:
                    if not ch.isspace() and self._expect:
                        self._expect = False           # `"options": null` ou outro valor: não é a lista
        return out

    def _string_char(self, ch: str) -> None:
        if self._esc:
            self._esc = False
        elif ch == "\\":
            self._esc = True
        elif ch == '"':
            self._in_str = False
            if self._key is not None:
                self._last, self._key = "".join(self._key), None
            return
        if self._key is not None:
            self._key.append(ch)

    def _finish(self, out: list[dict[str, Any]]) -> None:
        fragment, self._obj = "".join(self._obj or ()), None
        try:
            obj = json.loads(fragment)
        except ValueError:
            self.dropped += 1
            return
        if isinstance(obj, dict):
            out.append(obj)
        else:
            self.dropped += 1


__all__ = ["OPTIONS_KEY", "OptionsReader"]
