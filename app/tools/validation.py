"""JSON Schema 校验，错误翻成给模型看的短中文。"""

from typing import Any

from jsonschema import Draft202012Validator

_TYPES = {"string": "字符串", "integer": "整数", "number": "数字", "boolean": "布尔值",
          "array": "列表", "object": "对象", "null": "空"}


def _where(error) -> str:
    return ".".join(str(p) for p in error.absolute_path) or "参数"


def _message(error) -> list[str]:
    v, val, where = error.validator, error.validator_value, _where(error)
    if v == "required":
        return [f"缺少必填参数 {name}" for name in val if name not in (error.instance or {})]
    if v == "type":
        types = val if isinstance(val, list) else [val]
        return [f"{where} 类型应为{'或'.join(_TYPES.get(t, t) for t in types)}"]
    if v in ("enum", "const"):
        values = val if v == "enum" else [val]
        return [f"{where} 只能是 {'/'.join(str(x) for x in values)}"]
    if v == "pattern":
        return [f"{where} 格式不对"]
    if v == "minLength":
        return [f"{where} 长度不能少于 {val} 个字"]
    if v == "maxLength":
        return [f"{where} 长度不能超过 {val} 个字"]
    if v in ("minimum", "exclusiveMinimum"):
        return [f"{where} 不能小于 {val}"]
    if v in ("maximum", "exclusiveMaximum"):
        return [f"{where} 不能大于 {val}"]
    if v == "minItems":
        return [f"{where} 至少 {val} 项"]
    if v == "maxItems":
        return [f"{where} 最多 {val} 项"]
    if v == "uniqueItems":
        return [f"{where} 不能有重复项"]
    if v == "additionalProperties":
        return [f"不认识的参数：{error.message}"]
    return [f"{where} 取值不合法"]


def validate_args(schema: dict, args: Any) -> list[str]:
    if not isinstance(args, dict):
        return ["参数必须是 JSON 对象"]
    errors = sorted(Draft202012Validator(schema).iter_errors(args),
                    key=lambda e: list(map(str, e.absolute_path)))
    out: list[str] = []
    for error in errors:
        for msg in _message(error):
            if msg not in out:
                out.append(msg)
    return out
