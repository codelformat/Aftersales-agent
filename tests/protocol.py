"""校验 SSE 事件与前端共享的 JSON Schema。"""

import json
from functools import cache
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "web/src/protocol/events.schema.json"


@cache
def _validator():
    assert SCHEMA_PATH.is_file(), f"事件 schema 不存在：{SCHEMA_PATH}"
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate_event(name: str, data: dict) -> None:
    validator = _validator()
    event = {"event": name, "data": data}
    errors = list(validator.iter_errors(event))
    if not errors:
        return
    # 选择当前事件的分支，让失败路径指向业务字段。
    for branch in validator.schema["oneOf"]:
        if branch["properties"]["event"]["const"] == name:
            errors = list(validator.evolve(schema=branch).iter_errors(event)) or errors
            break
    error = best_match(errors)
    path = "/" + "/".join(str(part) for part in error.absolute_path)
    raise AssertionError(f"事件 {name} 在 {path} 不符合 schema：{error.message}")
