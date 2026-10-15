"""确定性 Case Adapter：校验显式映射，不从自然语言猜测请求或断言。"""

from ai_testexec.errors import GateError
from ai_testexec.io import digest, json_data, parse
from ai_testexec.models import (
    AutomatedPermit, ConversionOutcome, ConversionResult, ExecutableBundle, ExecutableCase, SourceArtifact,
)
from ai_testexec.review import (
    create_automated_permit, validate_inputs, verify_automated_permit, verify_review,
)


def fingerprint(case):
    """消除展示ID和变量命名差异，动态fixture的实际UUID不进入去重摘要。"""
    data = case.model_dump(mode="json")
    data.pop("source_case_id")
    variable_names = {}
    for fixture in data["fixtures"]:
        variable_names[fixture["name"]] = f"v{len(variable_names)}"
        fixture["name"] = variable_names[fixture["name"]]
    steps = {op["step_id"]: f"s{i}" for i, op in enumerate(data["operations"])}
    for op in data["operations"]:
        op["step_id"] = steps[op["step_id"]]
        if "request_id" in op:
            op["request_id"] = steps[op["request_id"]]
        if "captures" in op:
            captures = {}
            for name, pointer in op["captures"].items():
                variable_names[name] = f"v{len(variable_names)}"
                captures[variable_names[name]] = pointer
            op["captures"] = captures

    def rewrite(value):
        if isinstance(value, dict):
            # literal内部是普通业务JSON；即使形状像ref，也不属于可执行引用。
            if value.get("kind") == "literal" and set(value) == {"kind", "value"}:
                return value
            if value.get("kind") == "ref" and set(value) == {"kind", "name"}:
                return {"kind": "ref", "name": variable_names[value["name"]]}
            return {key: rewrite(nested) for key, nested in value.items()}
        if isinstance(value, list):
            return [rewrite(nested) for nested in value]
        return value

    return digest(rewrite(data))


class CaseAdapter:
    def adapt(self, source, mapping, openapi, review, *, seen=None):
        try:
            source, mapping, _ = validate_inputs(source, mapping, openapi)
            receipt = verify_review(source, mapping, openapi, review)
        except GateError as error:
            return ConversionResult(bundle=None, outcomes=[ConversionOutcome(
                source_case_id=None, status="conversion_failed", code=error.code, reason=str(error)
            )])
        except (KeyError, TypeError, ValueError, RecursionError) as error:
            # 契约本身格式不正确也不能进入执行阶段，不包含用户输入值的repr。
            return ConversionResult(bundle=None, outcomes=[ConversionOutcome(
                source_case_id=None, status="conversion_failed", code="contract_invalid",
                reason="无法解析或验证接口契约"
            )])
        by_case = {case.source_case_id: case for case in mapping.cases}
        seen = dict(seen or {})
        executable, outcomes = [], []
        for source_case in source.test_suite.test_cases:
            mapped = by_case.get(source_case.case_id)
            if mapped is None or source_case.unverifiable_expectations:
                outcomes.append(ConversionOutcome(
                    source_case_id=source_case.case_id, status="needs_manual_mapping", code="needs_manual_mapping",
                    reason="缺少显式执行映射或源用例仍有无法验证的预期"
                ))
                continue
            identity = source.metadata.run_id, source_case.case_id
            content_fingerprint = fingerprint(mapped)
            if content_fingerprint in seen and tuple(seen[content_fingerprint]) != identity:
                outcomes.append(ConversionOutcome(
                    source_case_id=source_case.case_id, status="conversion_failed", code="duplicate_generated",
                    reason="该执行内容已由另一个生成用例提供"
                ))
                continue
            seen[content_fingerprint] = identity
            execution_id = digest({"source": digest(source), "case": source_case.case_id,
                                   "fingerprint": content_fingerprint})[:24]
            executable.append(ExecutableCase(
                execution_case_id=execution_id, generation_run_id=source.metadata.run_id,
                source_case_id=source_case.case_id, source_refs=source_case.source_refs,
                fingerprint=content_fingerprint, mapping=mapped,
            ))
            outcomes.append(ConversionOutcome(source_case_id=source_case.case_id, status="ready",
                                             code="ready", reason="审核、映射和契约校验通过"))
        bundle = ExecutableBundle(
            source=source, mapping=mapping, review_id=receipt.review_id,
            source_digest=digest(source), mapping_digest=digest(mapping), contract_digest=digest(openapi),
            cases=executable,
        ) if executable else None
        return ConversionResult(bundle=bundle, outcomes=outcomes)


def create_automated_bundle(source, mapping, openapi):
    """Validate a model plan and create a machine-authorized executable subset."""
    try:
        source, mapping, _ = validate_inputs(source, mapping, openapi)
        permit = create_automated_permit(source, mapping, openapi)
    except GateError as error:
        try:
            parsed_source = parse(SourceArtifact, source)
            source_cases = parsed_source.test_suite.test_cases
        except (TypeError, ValueError, AttributeError):
            source_cases = []
        outcomes = [ConversionOutcome(
            source_case_id=case.case_id,
            status="plan_invalid",
            code=error.code,
            reason=str(error),
        ) for case in source_cases]
        if not outcomes:
            outcomes = [ConversionOutcome(source_case_id=None, status="plan_invalid",
                                          code=error.code, reason=str(error))]
        return None, outcomes, None
    except (KeyError, TypeError, ValueError, RecursionError):
        return None, [ConversionOutcome(source_case_id=None, status="plan_invalid",
                                        code="plan_invalid", reason="执行计划或接口契约无法解析")], None

    bundle, outcomes = _build_automated_bundle(source, mapping, openapi, permit)
    return bundle, outcomes, permit if bundle is not None else None


def _build_automated_bundle(source, mapping, openapi, permit):
    by_case = {case.source_case_id: case for case in mapping.cases}
    executable, outcomes, seen = [], [], {}
    for source_case in source.test_suite.test_cases:
        mapped = by_case.get(source_case.case_id)
        if mapped is None:
            outcomes.append(ConversionOutcome(
                source_case_id=source_case.case_id, status="plan_invalid", code="plan_missing",
                reason="第二阶段未提供该用例的可执行计划",
            ))
            continue
        if source_case.unverifiable_expectations:
            outcomes.append(ConversionOutcome(
                source_case_id=source_case.case_id, status="plan_invalid", code="unverifiable_expectation",
                reason="源用例仍包含无法验证的预期",
            ))
            continue
        content_fingerprint = fingerprint(mapped)
        if content_fingerprint in seen:
            outcomes.append(ConversionOutcome(
                source_case_id=source_case.case_id, status="plan_invalid", code="duplicate_generated",
                reason="该执行内容与本次另一条用例重复",
            ))
            continue
        seen[content_fingerprint] = source_case.case_id
        execution_id = digest({"source": digest(source), "case": source_case.case_id,
                               "fingerprint": content_fingerprint})[:24]
        executable.append(ExecutableCase(
            execution_case_id=execution_id, generation_run_id=source.metadata.run_id,
            source_case_id=source_case.case_id, source_refs=source_case.source_refs,
            fingerprint=content_fingerprint, mapping=mapped,
        ))
        outcomes.append(ConversionOutcome(source_case_id=source_case.case_id, status="ready",
                                          code="ready", reason="自动计划、映射和接口契约校验通过"))
    if not executable:
        return None, outcomes
    return ExecutableBundle(
        source=source, mapping=mapping, automated_permit_id=permit.permit_id,
        source_digest=digest(source), mapping_digest=digest(mapping), contract_digest=digest(openapi),
        cases=executable,
    ), outcomes


def verify_automated_bundle(bundle, permit, openapi):
    """Re-run all local gates and reject edits to a machine-approved bundle."""
    try:
        bundle = parse(ExecutableBundle, bundle)
    except (TypeError, ValueError):
        raise GateError("schema_invalid", "自动执行文件字段无效") from None
    permit = verify_automated_permit(bundle.source, bundle.mapping, openapi, permit)
    if bundle.review_id is not None or bundle.automated_permit_id != permit.permit_id:
        raise GateError("permit_invalid", "执行文件与自动校验凭据不匹配")
    expected, outcomes = _build_automated_bundle(bundle.source, bundle.mapping, openapi, permit)
    if expected is None:
        raise GateError("plan_invalid", "执行计划不再满足自动执行条件")
    if digest(bundle) != digest(expected):
        raise GateError("permit_invalid", "可执行数据已被修改，必须重新校验")
    return bundle


def verify_bundle(bundle, review, openapi):
    """执行时重做转换，并对比完整派生数据；不能信任可编辑文件的ready标记。"""
    try:
        bundle = parse(ExecutableBundle, bundle)
    except (TypeError, ValueError):
        raise GateError("schema_invalid", "执行文件字段无效") from None
    result = CaseAdapter().adapt(bundle.source, bundle.mapping, openapi, review)
    if result.bundle is None:
        outcome = result.outcomes[0]
        raise GateError(outcome.code, outcome.reason)
    # 去重可能只输出子集；它必须是原映射中合法、已审核且内容未修改的子集。
    expected = result.bundle
    expected_cases = {case.execution_case_id: case for case in expected.cases}
    actual_ids = [case.execution_case_id for case in bundle.cases]
    if len(set(actual_ids)) != len(actual_ids) or any(identifier not in expected_cases for identifier in actual_ids):
        raise GateError("review_invalid", "执行用例列表与审核映射不一致")
    expected = expected.model_copy(update={"cases": [expected_cases[identifier] for identifier in actual_ids]})
    if digest(bundle) != digest(expected):
        raise GateError("review_invalid", "可执行数据已被修改，必须重新转换")
    return bundle
