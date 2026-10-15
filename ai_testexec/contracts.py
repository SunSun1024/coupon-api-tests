"""离线、有限的 OpenAPI 3.1 Schema 校验。未支持的约束显式拒绝，不隐式通过。"""

import math
import re
from datetime import datetime

from ai_testexec.errors import GateError
from ai_testexec.io import strict_equal
from ai_testexec.models import HttpRequest, AssertStatus, AssertJson, LiteralValue
from ai_testexec.policy import build_path


OPERATIONS = {
    "createCoupon": ("POST", "/coupons"),
    "claimCoupon": ("POST", "/users/{user_id}/coupons/{coupon_id}/claims"),
    "redeemCoupon": ("POST", "/users/{user_id}/coupons/{user_coupon_id}/redemptions"),
    "getUserCoupon": ("GET", "/users/{user_id}/coupons/{user_coupon_id}"),
}
ANNOTATIONS = {"title", "description", "example", "examples", "default", "deprecated", "readOnly", "writeOnly"}
SUPPORTED = ANNOTATIONS | {"$ref", "type", "properties", "required", "additionalProperties", "items",
    "minItems", "maxItems", "minLength", "maxLength", "minimum", "maximum", "exclusiveMinimum",
    "exclusiveMaximum", "enum", "const", "oneOf", "anyOf", "allOf", "format"}


def pointer_parts(pointer):
    if not isinstance(pointer, str) or (pointer and not pointer.startswith("/")):
        raise GateError("contract_invalid", "JSON Pointer 必须为空或以 / 开头")
    if re.search(r"~(?![01])", pointer):
        raise GateError("contract_invalid", "JSON Pointer 转义无效")
    return [p.replace("~1", "/").replace("~0", "~") for p in pointer[1:].split("/")] if pointer else []


def read_pointer(value, pointer):
    for part in pointer_parts(pointer):
        if isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]*", part):
            index = int(part)
            if index >= len(value):
                raise GateError("response_field_missing", f"响应字段不存在: {pointer}")
            value = value[index]
        elif isinstance(value, dict) and part in value:
            value = value[part]
        else:
            raise GateError("response_field_missing", f"响应字段不存在: {pointer}")
    return value


def check_schema(schema):
    if isinstance(schema, bool):
        return
    if not isinstance(schema, dict):
        raise GateError("contract_invalid", "Schema 必须是对象或布尔值")
    unknown = schema.keys() - SUPPORTED
    if unknown:
        raise GateError("contract_invalid", "未支持的Schema约束: " + ",".join(sorted(unknown)))
    types = schema.get("type", [])
    types = [types] if isinstance(types, str) else types
    if (not isinstance(types, list) or ("type" in schema and not types) or
        any(not isinstance(t, str) or t not in {"object", "array", "string", "integer", "number", "boolean", "null"} for t in types)):
        raise GateError("contract_invalid", "Schema type 不受支持")
    if len(set(types)) != len(types):
        raise GateError("contract_invalid", "Schema type重复")
    if schema.get("format") not in {None, "date-time"}:
        raise GateError("contract_invalid", "未支持的Schema format")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict) or not isinstance(schema.get("required", []), list):
        raise GateError("contract_invalid", "Schema properties/required 无效")
    required = schema.get("required", [])
    if any(not isinstance(name, str) for name in required) or len(set(required)) != len(required):
        raise GateError("contract_invalid", "Schema required必须是不同的字段名称")
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        raise GateError("contract_invalid", "Schema enum不能为空")
    for key in ("minLength", "maxLength", "minItems", "maxItems"):
        if key in schema and (type(schema[key]) is not int or schema[key] < 0):
            raise GateError("contract_invalid", f"Schema {key}必须是非负整数")
    for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
        if key in schema and (type(schema[key]) not in {int, float} or not math.isfinite(schema[key])):
            raise GateError("contract_invalid", f"Schema {key}必须是有限数值")
    for nested in properties.values():
        check_schema(nested)
    for key in ("items", "additionalProperties"):
        if key in schema:
            check_schema(schema[key])
    for key in ("oneOf", "anyOf", "allOf"):
        if key in schema:
            if not isinstance(schema[key], list) or not schema[key]:
                raise GateError("contract_invalid", f"Schema {key} 无效")
            for nested in schema[key]:
                check_schema(nested)


def validate_value(value, schema, location="value"):
    def fail(message):
        raise GateError("contract_invalid", f"{location}: {message}")
    if schema is True:
        return
    if schema is False:
        fail("Schema拒绝该值")
    types = schema.get("type")
    checks = {"object": isinstance(value, dict), "array": isinstance(value, list),
              "string": isinstance(value, str), "integer": type(value) is int,
              "number": type(value) in {int, float} and math.isfinite(value),
              "boolean": type(value) is bool, "null": value is None}
    if types and not any(checks[t] for t in ([types] if isinstance(types, str) else types)):
        fail("字段类型不符合Schema")
    for key in ("oneOf", "anyOf", "allOf"):
        if key in schema:
            matches = 0
            for nested in schema[key]:
                try:
                    validate_value(value, nested, location)
                    matches += 1
                except GateError:
                    pass
            if (key == "oneOf" and matches != 1) or (key == "anyOf" and matches < 1) or (key == "allOf" and matches != len(schema[key])):
                fail(f"不符合{key}")
    if "const" in schema and not strict_equal(value, schema["const"]):
        fail("不符合const")
    if "enum" in schema and not any(strict_equal(value, x) for x in schema["enum"]):
        fail("不符合enum")
    if isinstance(value, dict):
        missing = set(schema.get("required", [])) - value.keys()
        if missing:
            fail("缺少字段: " + ",".join(sorted(missing)))
        for name, item in value.items():
            nested = schema.get("properties", {}).get(name, schema.get("additionalProperties", True))
            validate_value(item, nested, location + "." + name)
    if isinstance(value, list):
        for key, op in (("minItems", lambda n: len(value) >= n), ("maxItems", lambda n: len(value) <= n)):
            if key in schema and not op(schema[key]):
                fail(key)
        if "items" in schema:
            for index, item in enumerate(value):
                validate_value(item, schema["items"], f"{location}[{index}]")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", float("inf")):
            fail("字符串长度越界")
        if schema.get("format") == "date-time":
            try:
                # fromisoformat会把+00:60归一化成+01:00，不能独自承担契约检查。
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-](?:[01]\d|2[0-3]):[0-5]\d)", value):
                    fail("日期时间必须包含秒和时区")
                timestamp = datetime.fromisoformat(value.replace("Z", "+00:00").replace("z", "+00:00"))
                if timestamp.tzinfo is None:
                    fail("日期时间缺少时区")
            except ValueError:
                fail("日期时间格式无效")
    if type(value) in {int, float}:
        if not math.isfinite(value):
            fail("数字必须有限")
        for key, op in (("minimum", lambda n: value >= n), ("maximum", lambda n: value <= n),
                        ("exclusiveMinimum", lambda n: value > n), ("exclusiveMaximum", lambda n: value < n)):
            if key in schema and not op(schema[key]):
                fail(key)


def sample(schema):
    """转换期用类型样本检查引用；执行期始终再次检查真实值。"""
    if not isinstance(schema, dict):
        return None
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    if "oneOf" in schema or "anyOf" in schema:
        return sample(schema.get("oneOf", schema.get("anyOf"))[0])
    types = schema.get("type", "string")
    kind = types[0] if isinstance(types, list) else types
    if kind == "string":
        return "2030-01-01T00:00:00Z" if schema.get("format") == "date-time" else "preview-id"
    if kind in {"integer", "number"}:
        return max(1, schema.get("minimum", 1), schema.get("exclusiveMinimum", 0) + 1)
    if kind == "boolean":
        return True
    if kind == "object":
        return {key: sample(schema.get("properties", {})[key]) for key in schema.get("required", [])}
    if kind == "array":
        return [sample(schema.get("items", True)) for _ in range(schema.get("minItems", 0))]
    return None


def resolve_value(value, context):
    if isinstance(value, LiteralValue):
        return value.value
    if value.name not in context:
        raise GateError("contract_invalid", f"引用了未定义或尚不可用的变量: {value.name}")
    return context[value.name]


def serialize_query(values):
    """首期只发送明确的标量query；拒绝Requests会省略或错误编码的值。"""
    result = {}
    for name, value in values.items():
        if type(value) not in {str, int, float, bool} or (type(value) is float and not math.isfinite(value)):
            raise GateError("contract_invalid", "query序列化只支持非null的有限标量")
        result[name] = ("true" if value else "false") if type(value) is bool else str(value)
    return result


class Contract:
    def __init__(self, document):
        if not isinstance(document, dict) or not str(document.get("openapi", "")).startswith("3.1."):
            raise GateError("contract_invalid", "只支持本地 OpenAPI 3.1 契约")
        self.document = document
        self.operations = {}
        self._check_refs(document)
        for name, (method, path) in OPERATIONS.items():
            operation = document.get("paths", {}).get(path, {}).get(method.lower())
            if not isinstance(operation, dict) or operation.get("operationId") != name:
                raise GateError("contract_invalid", f"契约缺少已登记接口: {name}")
            self.operations[name] = operation
            if operation.get("security", document.get("security", [])):
                raise GateError("contract_invalid", "首期执行器未开放认证接口")
            for parameter in [*document["paths"][path].get("parameters", []), *operation.get("parameters", [])]:
                parameter = self.resolve(parameter)
                if parameter.get("in") not in {"path", "query"}:
                    raise GateError("contract_invalid", "首期不支持header/cookie参数")
                if not isinstance(parameter.get("name"), str) or type(parameter.get("required", False)) is not bool:
                    raise GateError("contract_invalid", "参数名称或required无效")
                place = parameter["in"]
                style, explode = ("form", True) if place == "query" else ("simple", False)
                if (parameter.get("style", style) != style or
                    type(parameter.get("explode", explode)) is not bool or parameter.get("explode", explode) != explode or
                    parameter.get("allowReserved", False) is not False):
                    raise GateError("contract_invalid", "未支持的参数序列化配置")
                check_schema(self.resolve(parameter.get("schema")))
            self.request_schema(name)
            for status in operation.get("responses", {}):
                self.response_schema(name, int(status))

    def _check_refs(self, value, depth=0):
        if depth > 80:
            raise GateError("contract_invalid", "接口契约嵌套过深")
        if isinstance(value, dict):
            if "$ref" in value and (not isinstance(value["$ref"], str) or not value["$ref"].startswith("#/")):
                raise GateError("contract_invalid", "外部Schema引用不允许")
            for nested in value.values():
                self._check_refs(nested, depth + 1)
        elif isinstance(value, list):
            for nested in value:
                self._check_refs(nested, depth + 1)

    def resolve(self, value, stack=()):
        if len(stack) > 40:
            raise GateError("contract_invalid", "Schema引用过深")
        if isinstance(value, dict):
            if "$ref" in value:
                reference = value["$ref"]
                if reference in stack:
                    raise GateError("contract_invalid", "递归Schema暂不支持")
                target = read_pointer(self.document, reference[1:])
                resolved = self.resolve(target, (*stack, reference))
                if not isinstance(resolved, dict):
                    raise GateError("contract_invalid", "引用目标不是Schema对象")
                siblings = {key: nested for key, nested in value.items() if key != "$ref"}
                if siblings:
                    # 组合而非覆盖，保留$ref目标和其兄弟约束的交集。
                    return {"allOf": [resolved, self.resolve(siblings, stack)]}
                return resolved
            return {key: self.resolve(nested, stack) for key, nested in value.items()}
        if isinstance(value, list):
            return [self.resolve(nested, stack) for nested in value]
        return value

    def request_schema(self, operation_id):
        body = self.operations[operation_id].get("requestBody")
        if body is None:
            return None
        body = self.resolve(body)
        if type(body.get("required", False)) is not bool:
            raise GateError("contract_invalid", "requestBody.required必须是布尔值")
        schema = body.get("content", {}).get("application/json", {}).get("schema")
        if schema is None:
            raise GateError("contract_invalid", "请求体缺少JSON Schema")
        check_schema(schema)
        return schema

    def response_schema(self, operation_id, status):
        response = self.operations[operation_id].get("responses", {}).get(str(status))
        if response is None:
            raise GateError("contract_invalid", f"未定义响应状态: {status}")
        response = self.resolve(response)
        schema = response.get("content", {}).get("application/json", {}).get("schema")
        if schema is None:
            raise GateError("contract_invalid", "响应缺少JSON Schema")
        check_schema(schema)
        return schema

    def pointer_schema(self, schema, pointer):
        for part in pointer_parts(pointer):
            if not isinstance(schema, dict):
                raise GateError("contract_invalid", "无法确定响应字段类型")
            if part in schema.get("properties", {}):
                schema = schema["properties"][part]
            elif "items" in schema and re.fullmatch(r"0|[1-9][0-9]*", part):
                schema = schema["items"]
            else:
                raise GateError("contract_invalid", f"未定义响应字段: {pointer}")
        return schema

    def validate_request(self, op, path_params, body, query):
        if OPERATIONS.get(op.operation_id) != (op.method, op.path):
            raise GateError("contract_invalid", "Method/path/operation_id 不在登记表内")
        build_path(op.path, path_params)
        operation = self.operations[op.operation_id]
        path_item = self.document["paths"][op.path]
        definitions = [self.resolve(p) for p in [*path_item.get("parameters", []), *operation.get("parameters", [])]]
        for place, values in (("path", path_params), ("query", query)):
            permitted = {p["name"]: p for p in definitions if p.get("in") == place}
            if values.keys() - permitted.keys():
                raise GateError("contract_invalid", f"未定义的{place}参数")
            for name, definition in permitted.items():
                if definition.get("required") and name not in values:
                    raise GateError("contract_invalid", f"缺少{place}参数: {name}")
                if name in values:
                    schema = definition.get("schema")
                    check_schema(schema)
                    validate_value(values[name], schema, place + "." + name)
        serialize_query(query)
        schema = self.request_schema(op.operation_id)
        if schema is None and body is not None:
            raise GateError("contract_invalid", "接口不接受请求体")
        # 映射中的null表示不发送请求体，不能混同JSON正文null。
        definition = self.resolve(operation.get("requestBody"))
        if definition is not None and definition.get("required", False) and body is None:
            raise GateError("contract_invalid", "缺少必需的请求体")
        if schema is not None and body is not None:
            validate_value(body, schema, "body")

    def validate_mapping(self, case):
        context, requests, names = {}, {}, set()
        for fixture in case.fixtures:
            if fixture.name in context:
                raise GateError("contract_invalid", "变量名称重复")
            context[fixture.name] = "2030-01-01T00:00:00Z" if fixture.factory == "expires_at" else "preview-id"
        for index, op in enumerate(case.operations):
            if op.step_id in names:
                raise GateError("contract_invalid", "step_id重复")
            names.add(op.step_id)
            if isinstance(op, HttpRequest):
                following = case.operations[index + 1] if index + 1 < len(case.operations) else None
                if not isinstance(following, AssertStatus) or following.request_id != op.step_id:
                    raise GateError("contract_invalid", "每个请求必须紧跟对应状态码断言")
                params = {k: resolve_value(v, context) for k, v in op.path_params.items()}
                body = None if op.json_body is None else {k: resolve_value(v, context) for k, v in op.json_body.items()}
                query = {k: resolve_value(v, context) for k, v in op.query.items()}
                self.validate_request(op, params, body, query)
                schema = self.response_schema(op.operation_id, following.expected)
                requests[op.step_id] = schema
                for name, pointer in op.captures.items():
                    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name) or name in context:
                        raise GateError("contract_invalid", "提取变量名称无效或重复")
                    context[name] = sample(self.pointer_schema(schema, pointer))
            elif op.request_id not in requests:
                raise GateError("contract_invalid", "断言引用尚未发生的请求")
            elif isinstance(op, AssertJson):
                validate_value(resolve_value(op.expected, context), self.pointer_schema(requests[op.request_id], op.pointer), "assert.expected")
        if not requests:
            raise GateError("contract_invalid", "执行映射必须包含HTTP请求")
