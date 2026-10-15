import copy

import pytest

from ai_testexec.contracts import Contract, check_schema, validate_value, read_pointer
from ai_testexec.errors import GateError
from tests.support.ai_case_examples import contract

pytestmark = pytest.mark.regression


@pytest.mark.parametrize("value,schema", [
    (True, {"type": "integer"}), ("10", {"type": "integer"}),
    (-1, {"type": "integer", "minimum": 0}), (0, {"type": "integer", "exclusiveMinimum": 0}),
    ({}, {"type": "object", "required": ["order_id"]}),
    ({"secret": 1}, {"type": "object", "additionalProperties": False}),
    ("x", {"type": "string", "minLength": 2}),
    ("2030-01-01T01:00+00:00", {"type": "string", "format": "date-time"}),
    ("2030-01-01T00:00:00", {"type": "string", "format": "date-time"}),
    ("2030-01-01T00:00:00+00:60", {"type": "string", "format": "date-time"}),
    ("2030-01-01T00:00:00-23:99", {"type": "string", "format": "date-time"}),
    ([1], {"type": "array", "items": {"type": "string"}}),
    (1, {"oneOf": [{"type": "integer"}, {"type": "number"}]}),
])
def test_supported_constraints_fail_invalid_values(value, schema):
    check_schema(schema)
    with pytest.raises(GateError):
        validate_value(value, schema)


@pytest.mark.parametrize("schema", [
    {"type": "string", "pattern": "^x$"}, {"type": []}, {"enum": []},
    {"minLength": -1}, {"required": [1]}, {"minimum": "0"},
    {"format": "email"}, {"type": "object", "dependentRequired": {"a": ["b"]}},
])
def test_unsupported_or_malformed_schema_is_not_silently_accepted(schema):
    with pytest.raises(GateError):
        check_schema(schema)


def test_oneof_nullable_anyof_and_local_refs_work():
    schema = {"anyOf": [{"type": "null"}, {"type": "integer", "minimum": 0}]}
    validate_value(None, schema)
    validate_value(10, schema)
    resolved = Contract(contract()).request_schema("redeemCoupon")
    validate_value({"order_id": "order-1", "order_amount_cents": 10000}, resolved)
    assert read_pointer({"a/b": {"x~y": [7]}}, "/a~1b/x~0y/0") == 7


def test_recursive_ref_is_refused():
    document = contract()
    document["components"]["schemas"]["RedemptionRequest"] = {"$ref": "#/components/schemas/RedemptionRequest"}
    with pytest.raises(GateError, match="递归"):
        Contract(document)


def test_required_header_is_refused_until_supported():
    document = contract()
    document["paths"]["/coupons"]["post"]["parameters"] = [{"name": "Authorization", "in": "header", "required": True, "schema": {"type": "string"}}]
    with pytest.raises(GateError):
        Contract(document)


def test_required_body_cannot_be_omitted_even_if_schema_accepts_null(tmp_path):
    from ai_testexec.adapter import CaseAdapter
    from tests.support.ai_case_examples import generate_source, mapping
    source = generate_source(tmp_path / "generated")
    plan, document = mapping(source), contract()
    document["components"]["schemas"]["CouponCreateRequest"]["type"] = ["object", "null"]
    plan["cases"][0]["operations"][0]["json_body"] = None
    result = CaseAdapter().adapt(source, plan, document, None)
    assert result.bundle is None
    assert result.outcomes[0].code == "contract_invalid"


def test_query_null_is_rejected_instead_of_silently_omitted(tmp_path):
    from ai_testexec.adapter import CaseAdapter
    from tests.support.ai_case_examples import generate_source, mapping, literal
    source = generate_source(tmp_path / "generated")
    plan, document = mapping(source), contract()
    document["paths"]["/users/{user_id}/coupons/{user_coupon_id}"]["get"]["parameters"].append(
        {"name": "filter", "in": "query", "required": True, "schema": {"type": ["string", "null"]}}
    )
    for op in plan["cases"][0]["operations"]:
        if op.get("operation_id") == "getUserCoupon":
            op["query"] = {"filter": literal(None)}
    result = CaseAdapter().adapt(source, plan, document, None)
    assert result.bundle is None
    assert result.outcomes[0].code == "contract_invalid"


def test_deepobject_serialization_is_rejected_instead_of_wrongly_encoded():
    document = contract()
    document["paths"]["/users/{user_id}/coupons/{user_coupon_id}"]["get"]["parameters"].append(
        {"name": "filter", "in": "query", "style": "deepObject", "explode": True,
         "schema": {"type": "object", "properties": {"status": {"type": "string"}}}}
    )
    with pytest.raises(GateError, match="序列化"):
        Contract(document)
