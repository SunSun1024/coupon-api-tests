"""审核与内容绑定。只有显式调用审核命令才能生成独立收据。"""

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import ValidationError

from ai_testgen.validator import validate_suite, BusinessRuleValidationError, OpenAPIDocumentError
from ai_testexec.contracts import Contract
from ai_testexec.errors import GateError
from ai_testexec.io import digest, parse
from ai_testexec.models import AutomatedPermit, SourceArtifact, MappingBundle, ReviewReceipt


AUTOMATED_VALIDATOR_VERSION = "coupon-plan-validator-v1"


def validate_inputs(source, mapping, openapi):
    try:
        source = parse(SourceArtifact, source)
        mapping = parse(MappingBundle, mapping)
    except (ValidationError, TypeError, ValueError) as error:
        locations = [".".join(map(str, e["loc"])) + ":" + e["type"] for e in error.errors()] if isinstance(error, ValidationError) else ["input"]
        raise GateError("schema_invalid", "输入字段无效: " + ", ".join(locations)) from None
    if source.metadata.run_id != mapping.source_run_id:
        raise GateError("contract_invalid", "映射的生成运行ID与源文件不一致")
    try:
        report = validate_suite(source.test_suite, source_catalog=source.source_catalog, openapi_document=openapi)
    except (BusinessRuleValidationError, OpenAPIDocumentError) as error:
        raise GateError("schema_invalid", str(error)) from None
    if report != source.validation:
        raise GateError("schema_invalid", "源文件校验报告与重新校验结果不一致")
    contract = Contract(openapi)
    ids = {case.case_id for case in source.test_suite.test_cases}
    seen = set()
    for case in mapping.cases:
        if case.source_case_id not in ids:
            raise GateError("contract_invalid", "映射引用未知源用例")
        if case.source_case_id in seen:
            raise GateError("duplicate_generated", "同一源用例重复映射")
        seen.add(case.source_case_id)
        contract.validate_mapping(case)
    return source, mapping, contract


def create_review(source, mapping, openapi, *, reviewer, acknowledged_warnings=False):
    source, mapping, _ = validate_inputs(source, mapping, openapi)
    if source.validation.status == "passed_with_warnings" and not acknowledged_warnings:
        raise GateError("review_required", "请先人工审查警告，并显式确认已知警告")
    return ReviewReceipt(
        review_id=uuid4().hex, reviewer=reviewer, reviewed_at=datetime.now(timezone.utc),
        source_digest=digest(source), mapping_digest=digest(mapping), contract_digest=digest(openapi),
        acknowledged_warnings=acknowledged_warnings,
    )


def verify_review(source, mapping, openapi, receipt):
    if receipt is None:
        raise GateError("review_required", "未提供独立人工审核记录")
    try:
        receipt = parse(ReviewReceipt, receipt)
    except (ValueError, TypeError):
        raise GateError("review_invalid", "审核记录字段无效") from None
    expected = digest(source), digest(mapping), digest(openapi)
    actual = receipt.source_digest, receipt.mapping_digest, receipt.contract_digest
    if actual != expected:
        raise GateError("review_invalid", "源用例、映射或接口契约已变化，原审核失效")
    if source.validation.status == "passed_with_warnings" and not receipt.acknowledged_warnings:
        raise GateError("review_invalid", "审核未确认源用例的警告")
    return receipt


def create_automated_permit(source, mapping, openapi):
    """Record successful local checks without representing them as human review."""
    source, mapping, _ = validate_inputs(source, mapping, openapi)
    return AutomatedPermit(
        permit_id=uuid4().hex,
        validator_version=AUTOMATED_VALIDATOR_VERSION,
        source_digest=digest(source),
        mapping_digest=digest(mapping),
        contract_digest=digest(openapi),
    )


def verify_automated_permit(source, mapping, openapi, permit):
    """Repeat the checks and ensure the plan still matches its machine permit."""
    source, mapping, _ = validate_inputs(source, mapping, openapi)
    try:
        permit = parse(AutomatedPermit, permit)
    except (ValueError, TypeError):
        raise GateError("permit_invalid", "自动校验凭据字段无效") from None
    expected = digest(source), digest(mapping), digest(openapi)
    actual = permit.source_digest, permit.mapping_digest, permit.contract_digest
    if permit.validator_version != AUTOMATED_VALIDATOR_VERSION or actual != expected:
        raise GateError("permit_invalid", "来源、执行计划、接口契约或校验器版本已变化")
    return permit
