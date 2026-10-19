"""A recursively validated, deliberately bounded JSON Schema vocabulary."""

from __future__ import annotations

import json
import math
from typing import Any, Mapping


class SchemaError(ValueError):
    pass


SCHEMA_GUIDANCE = (
    "Supported schema keywords: type (including nullable type arrays), properties, "
    "required, additionalProperties (boolean), items (one schema), enum, const, "
    "minimum, maximum, exclusiveMinimum, exclusiveMaximum, minLength, maxLength, "
    "minItems, maxItems and uniqueItems. Annotations title, description, default, "
    "examples and x-* are allowed. Do not use $ref, format, pattern, oneOf or anyOf."
)
_TYPES = {"object", "array", "string", "number", "integer", "boolean", "null"}
_ANNOTATIONS = {"title", "description", "default", "examples", "$schema"}
_TYPE_KEYS = {
    "object": {"properties", "required", "additionalProperties"},
    "array": {"items", "minItems", "maxItems", "uniqueItems"},
    "string": {"minLength", "maxLength"},
    "number": {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"},
    "integer": {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"},
}


def schema_types(schema: Mapping[str, Any]) -> list[str]:
    value = schema.get("type", "object" if "properties" in schema else "array" if "items" in schema else "string")
    return value if isinstance(value, list) else [value]


def _numeric(value: Any) -> bool:
    return (isinstance(value, int) and not isinstance(value, bool)) or (isinstance(value, float) and math.isfinite(value))


def validate_schema(schema: Mapping[str, Any], *, path: str = "$", depth: int = 0) -> dict[str, Any]:
    if not isinstance(schema, Mapping):
        raise SchemaError(f"{path}: schema must be an object")
    if depth > 32:
        raise SchemaError(f"{path}: schema nesting exceeds 32 levels")
    types = schema_types(schema)
    if not types or any(not isinstance(item, str) or item not in _TYPES for item in types) or len(set(types)) != len(types):
        raise SchemaError(f"{path}: unsupported or duplicate schema type")
    allowed = {"type", "enum", "const"} | _ANNOTATIONS
    for kind in types:
        allowed |= _TYPE_KEYS.get(kind, set())
    unknown = [key for key in schema if not isinstance(key, str) or (key not in allowed and not key.startswith("x-"))]
    if unknown:
        raise SchemaError(f"{path}: unsupported schema keywords: {unknown}")
    result = dict(schema)
    result.setdefault("type", types[0])
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        raise SchemaError(f"{path}: enum must be a non-empty array")
    if "object" in types:
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping) or any(not isinstance(name, str) or not name.strip() for name in properties):
            raise SchemaError(f"{path}: properties must map non-empty names to schemas")
        required = schema.get("required", [])
        if not isinstance(required, list) or any(not isinstance(name, str) for name in required):
            raise SchemaError(f"{path}: required must be an array of field names")
        if len(set(required)) != len(required) or set(required) - set(properties):
            raise SchemaError(f"{path}: required fields must be unique and declared in properties")
        if "additionalProperties" in schema and not isinstance(schema["additionalProperties"], bool):
            raise SchemaError(f"{path}: additionalProperties must be boolean")
        if "properties" in schema:
            result["properties"] = {name: validate_schema(value, path=f"{path}.{name}", depth=depth + 1) for name, value in properties.items()}
    if "items" in schema:
        result["items"] = validate_schema(schema["items"], path=f"{path}[]", depth=depth + 1)
    if "uniqueItems" in schema and not isinstance(schema["uniqueItems"], bool):
        raise SchemaError(f"{path}: uniqueItems must be boolean")
    for name in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
        if name in schema and not _numeric(schema[name]):
            raise SchemaError(f"{path}: {name} must be a finite number")
    for name in ("minLength", "maxLength", "minItems", "maxItems"):
        if name in schema and (not isinstance(schema[name], int) or isinstance(schema[name], bool) or schema[name] < 0):
            raise SchemaError(f"{path}: {name} must be a non-negative integer")
    for lower, upper in (("minimum", "maximum"), ("exclusiveMinimum", "exclusiveMaximum"), ("minLength", "maxLength"), ("minItems", "maxItems")):
        if lower in schema and upper in schema and schema[lower] > schema[upper]:
            raise SchemaError(f"{path}: {lower} exceeds {upper}")
    try:
        json.dumps(result, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise SchemaError(f"{path}: schema must contain finite JSON values") from exc
    return result


def _matches_type(value: Any, kind: str) -> bool:
    return {
        "null": lambda: value is None,
        "boolean": lambda: isinstance(value, bool),
        "string": lambda: isinstance(value, str),
        "number": lambda: _numeric(value),
        "integer": lambda: _numeric(value) and value == int(value),
        "object": lambda: isinstance(value, dict),
        "array": lambda: isinstance(value, list),
    }[kind]()


def _json_equal(left: Any, right: Any) -> bool:
    # Python considers True == 1, unlike JSON Schema equality.
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_json_equal(left[key], right[key]) for key in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_json_equal(a, b) for a, b in zip(left, right))
    return left == right


def validate_instance(value: Any, schema: Mapping[str, Any], *, path: str = "$") -> list[str]:
    types = schema_types(schema)
    if not any(_matches_type(value, kind) for kind in types):
        return [f"{path}: must have type {' or '.join(types)}"]
    errors: list[str] = []
    if "enum" in schema and not any(_json_equal(value, item) for item in schema["enum"]):
        errors.append(f"{path}: value is not in enum")
    if "const" in schema and not _json_equal(value, schema["const"]):
        errors.append(f"{path}: value does not match const")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        errors.extend(f"{path}.{name}: missing required field" for name in schema.get("required", []) if name not in value)
        if schema.get("additionalProperties") is False:
            errors.extend(f"{path}.{name}: unexpected field" for name in value if name not in properties)
        for name, child in properties.items():
            if name in value:
                errors.extend(validate_instance(value[name], child, path=f"{path}.{name}"))
    if isinstance(value, list):
        if "items" in schema:
            for index, item in enumerate(value):
                errors.extend(validate_instance(item, schema["items"], path=f"{path}[{index}]"))
        if schema.get("uniqueItems") and any(_json_equal(item, prior) for index, item in enumerate(value) for prior in value[:index]):
            errors.append(f"{path}: array items must be unique")
    if isinstance(value, (str, list)):
        for keyword, limit_ok in (("minLength" if isinstance(value, str) else "minItems", lambda a, b: a >= b), ("maxLength" if isinstance(value, str) else "maxItems", lambda a, b: a <= b)):
            if keyword in schema and not limit_ok(len(value), schema[keyword]):
                errors.append(f"{path}: violates {keyword}={schema[keyword]}")
    if _numeric(value):
        for keyword, limit_ok in (("minimum", lambda a, b: a >= b), ("maximum", lambda a, b: a <= b), ("exclusiveMinimum", lambda a, b: a > b), ("exclusiveMaximum", lambda a, b: a < b)):
            if keyword in schema and not limit_ok(value, schema[keyword]):
                errors.append(f"{path}: violates {keyword}={schema[keyword]}")
    return errors
