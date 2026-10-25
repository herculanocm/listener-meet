"""Esquemas JSON do projeto e validação própria (CP2, contrato "Saída estruturada"): subconjunto estrito aceito pelos três
SDKs (`additionalProperties: false`, todas as chaves em `required`, nulo como união `["string", "null"]`, sem `format`,
`minLength` nem `pattern`), `validate()` com coerção int/float e erro com caminho, `check_strict()` que confere o
subconjunto, `schema_hash()` gravado em `llm_calls`, `parse_json_text()` que tolera cercas de código. Sem pydantic."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

ALLOWED_KEYS = frozenset({"type", "properties", "required", "additionalProperties", "items", "enum", "maxLength", "description",
                          "minimum", "maximum", "minItems", "maxItems", "title"})
FORBIDDEN_KEYS = frozenset({"format", "minLength", "pattern", "$ref", "$defs", "anyOf", "oneOf", "allOf", "patternProperties", "default"})
# chaves que o modo estrito da OpenAI pode recusar: os adaptadores as tiram antes de enviar (a validação do projeto as aplica)
PROVIDER_SOFT_KEYS = frozenset({"maxLength", "minimum", "maximum", "minItems", "maxItems"})
JSON_TYPES = {"string": str, "integer": int, "number": float, "boolean": bool, "object": dict, "array": list, "null": type(None)}


class SchemaError(ValueError):
    def __init__(self, path: str, message: str):
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message


def _types(schema: Mapping[str, Any]) -> tuple[str, ...]:
    t = schema.get("type")
    if isinstance(t, str):
        return (t,)
    if isinstance(t, list | tuple):
        return tuple(str(x) for x in t)
    return ()


def validate(schema: Mapping[str, Any], data: Any, path: str = "$") -> Any:
    """Devolve uma cópia validada e coagida (int em `number`, `3.0` em `integer`, enum, `maxLength`); `SchemaError` com o
    caminho do primeiro problema. Chaves fora de `properties` são erro (o esquema é estrito)."""
    types = _types(schema)
    if data is None:
        if "null" in types:
            return None
        raise SchemaError(path, f"nulo onde se esperava {' ou '.join(types) or 'um valor'}")
    if "enum" in schema and data not in schema["enum"]:
        raise SchemaError(path, f"{data!r} fora das opções {schema['enum']}")
    if not types:
        return data
    for t in types:
        if t == "null":
            continue
        try:
            return _validate_as(t, schema, data, path)
        except SchemaError:
            if t == types[-1] or (len(types) == 2 and "null" in types):
                raise
    raise SchemaError(path, f"tipo inesperado {type(data).__name__}")


def _validate_as(t: str, schema: Mapping[str, Any], data: Any, path: str) -> Any:
    match t:
        case "string":
            if not isinstance(data, str):
                raise SchemaError(path, f"esperava texto, veio {type(data).__name__}")
            limit = schema.get("maxLength")
            if isinstance(limit, int) and len(data) > limit:
                raise SchemaError(path, f"texto com {len(data)} caracteres, acima de {limit}")
            return data
        case "integer":
            if isinstance(data, bool) or not isinstance(data, int | float):
                raise SchemaError(path, f"esperava inteiro, veio {type(data).__name__}")
            if isinstance(data, float):
                if not data.is_integer():
                    raise SchemaError(path, f"esperava inteiro, veio {data}")
                data = int(data)
            return _bounds(schema, int(data), path)
        case "number":
            if isinstance(data, bool) or not isinstance(data, int | float):
                raise SchemaError(path, f"esperava número, veio {type(data).__name__}")
            return _bounds(schema, float(data), path)
        case "boolean":
            if not isinstance(data, bool):
                raise SchemaError(path, f"esperava verdadeiro ou falso, veio {type(data).__name__}")
            return data
        case "array":
            if not isinstance(data, list):
                raise SchemaError(path, f"esperava lista, veio {type(data).__name__}")
            lo, hi = schema.get("minItems"), schema.get("maxItems")
            if isinstance(lo, int) and len(data) < lo:
                raise SchemaError(path, f"lista com {len(data)} itens, abaixo de {lo}")
            if isinstance(hi, int) and len(data) > hi:
                raise SchemaError(path, f"lista com {len(data)} itens, acima de {hi}")
            items = schema.get("items")
            if items is None:
                return list(data)
            return [validate(items, v, f"{path}[{i}]") for i, v in enumerate(data)]
        case "object":
            if not isinstance(data, Mapping):
                raise SchemaError(path, f"esperava objeto, veio {type(data).__name__}")
            props: Mapping[str, Any] = schema.get("properties") or {}
            out: dict[str, Any] = {}
            for key in schema.get("required") or props:
                if key not in data:
                    raise SchemaError(f"{path}.{key}", "chave obrigatória ausente")
            for key, value in data.items():
                if key not in props:
                    if schema.get("additionalProperties") is False:
                        raise SchemaError(f"{path}.{key}", "chave fora do esquema")
                    out[key] = value
                    continue
                out[key] = validate(props[key], value, f"{path}.{key}")
            return out
        case _:
            raise SchemaError(path, f"tipo de esquema desconhecido: {t}")


def _bounds(schema: Mapping[str, Any], value: Any, path: str) -> Any:
    lo, hi = schema.get("minimum"), schema.get("maximum")
    if lo is not None and value < lo:
        raise SchemaError(path, f"{value} abaixo do mínimo {lo}")
    if hi is not None and value > hi:
        raise SchemaError(path, f"{value} acima do máximo {hi}")
    return value


def check_strict(schema: Mapping[str, Any], path: str = "$") -> list[str]:
    """Problemas que fariam um dos SDKs recusar o esquema (lista vazia = dentro do subconjunto estrito)."""
    out: list[str] = []
    for key in schema:
        if key in FORBIDDEN_KEYS:
            out.append(f"{path}: chave proibida {key}")
        elif key not in ALLOWED_KEYS:
            out.append(f"{path}: chave desconhecida {key}")
    types = _types(schema)
    if not types:
        out.append(f"{path}: sem type")
    if len(types) > 2 or (len(types) == 2 and "null" not in types):
        out.append(f"{path}: união só com nulo ({types})")
    if "object" in types:
        props = schema.get("properties")
        if not isinstance(props, Mapping) or not props:
            out.append(f"{path}: objeto sem properties")
            props = {}
        if schema.get("additionalProperties") is not False:
            out.append(f"{path}: additionalProperties precisa ser false")
        required = schema.get("required")
        if not isinstance(required, list) or set(required) != set(props):
            out.append(f"{path}: required precisa listar todas as chaves")
        for key, sub in props.items():
            out += check_strict(sub, f"{path}.{key}")
    if "array" in types:
        items = schema.get("items")
        if not isinstance(items, Mapping):
            out.append(f"{path}: lista sem items")
        else:
            out += check_strict(items, f"{path}[]")
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        out.append(f"{path}: enum vazio")
    return out


def canonical(schema: Mapping[str, Any]) -> str:
    return json.dumps(schema, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def schema_hash(schema: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical(schema).encode("utf-8")).hexdigest()[:12]


def provider_schema(schema: Mapping[str, Any], drop: frozenset[str] = PROVIDER_SOFT_KEYS) -> dict[str, Any]:
    """Cópia sem as chaves que um provedor pode recusar (`maxLength` etc.); o projeto valida com o esquema inteiro."""
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key in drop:
            continue
        if key == "properties" and isinstance(value, Mapping):
            out[key] = {k: provider_schema(v, drop) for k, v in value.items()}
        elif key == "items" and isinstance(value, Mapping):
            out[key] = provider_schema(value, drop)
        else:
            out[key] = value
    return out


def parse_json_text(text: str) -> Any:
    """JSON da resposta; tolera cerca de código (```json ... ```) e texto antes ou depois do primeiro objeto ou lista."""
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
        s = s.strip()
    try:
        return json.loads(s)
    except ValueError:
        pass
    start = min((i for i in (s.find("{"), s.find("[")) if i >= 0), default=-1)
    end = max(s.rfind("}"), s.rfind("]"))
    if start < 0 or end <= start:
        raise ValueError("resposta sem JSON")
    return json.loads(s[start:end + 1])


def minimal_instance(schema: Mapping[str, Any]) -> Any:
    """Uma instância mínima válida do esquema (listas vazias, texto vazio, nulo nas uniões, primeiro enum): o fake usa."""
    types = _types(schema)
    if "enum" in schema:
        return schema["enum"][0]
    if "null" in types:
        return None
    match types[0] if types else "string":
        case "object":
            return {k: minimal_instance(v) for k, v in (schema.get("properties") or {}).items()}
        case "array":
            return [minimal_instance(schema["items"]) for _ in range(int(schema.get("minItems") or 0))]
        case "integer":
            return int(schema.get("minimum") or 0)
        case "number":
            return float(schema.get("minimum") or 0.0)
        case "boolean":
            return False
        case _:
            return ""
