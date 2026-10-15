"""执行 Pydantic 字段检查之外的跨用例、来源和 OpenAPI 业务校验。"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

from ai_testgen.models import HttpMethod, SourceReference, TestCase, TestSuite, ValidationReport


class BusinessRuleValidationError(ValueError):
    """结构正确但不满足业务/来源/API 一致性检查时抛出。"""

    def __init__(self, issues: Iterable[str]):
        self.issues = tuple(issues)
        super().__init__("; ".join(self.issues))


class OpenAPIDocumentError(ValueError):
    """OpenAPI 文档结构不适合建立 operation 索引时抛出。"""


def validate_suite(
    suite: TestSuite,
    *,
    source_catalog: Iterable[SourceReference] = (),
    openapi_document: Mapping[str, Any] | None = None,
    max_test_cases: int | None = None,
) -> ValidationReport:
    """校验来源、步骤、重复用例，以及可选的 API 路由。

    字段类型和必填值已由 Pydantic 检查；这里处理必须跨用例或对照外部来源才能判断的规则。
    """

    # 把来源目录和 OpenAPI 转成便于查询的集合/字典，避免每条用例重复扫描原始文档。
    catalog = {source.source_id for source in source_catalog}
    operations = _index_openapi(openapi_document) if openapi_document is not None else None
    issues: list[str] = []
    warnings: list[str] = []
    seen_ids: set[str] = set()
    seen_fingerprints: set[str] = set()
    unverifiable: list[str] = []

    if max_test_cases is not None:
        if max_test_cases < 1:
            raise ValueError("max_test_cases must be positive")
        if len(suite.test_cases) > max_test_cases:
            issues.append("suite exceeds the maximum allowed test case count")

    for case_index, case in enumerate(suite.test_cases, start=1):
        # ID 重复会让报告和后续自动化引用不稳定；内容指纹还能发现“改名后的重复用例”。
        if case.case_id in seen_ids:
            issues.append(f"duplicate case_id at case index {case_index}")
        seen_ids.add(case.case_id)

        fingerprint = _case_fingerprint(case)
        if fingerprint in seen_fingerprints:
            issues.append(f"duplicate test cases detected at case index {case_index}")
        seen_fingerprints.add(fingerprint)

        unknown_sources = [source_id for source_id in case.source_refs if source_id not in catalog]
        if unknown_sources:
            # 错误只报用例位置，不回显模型或用户提供的 source_id 文本。
            issues.append(f"case index {case_index} references unknown source IDs")

        step_numbers = [step.step_number for step in case.steps]
        expected_numbers = list(range(1, len(case.steps) + 1))
        if step_numbers != expected_numbers:
            # 用例步骤必须从 1 连续递增，避免漏步骤或顺序号无法稳定展示。
            issues.append(
                f"case index {case_index} step_number values must be consecutive from 1"
            )

        for step in case.steps:
            if (step.api_path is None) != (step.http_method is None):
                # 路径和 Method 必须成对出现，否则无法唯一定位一个 API 操作。
                issues.append(
                    f"case index {case_index} step {step.step_number} must provide both api_path and http_method"
                )
                continue
            if step.api_path is None or step.http_method is None:
                if step.operation_id is not None:
                    issues.append(
                        f"case index {case_index} step {step.step_number} provides operationId without an API path and method"
                    )
                continue

            if operations is None:
                # 无接口定义时只声明“无法验证”，不把模型生成的路径假装成已确认。
                warning = (
                    f"case index {case_index} step {step.step_number}: API path/method could not be "
                    "verified because no OpenAPI document was supplied"
                )
                if warning not in warnings:
                    warnings.append(warning)
                continue

            # OpenAPI path 参数保留 `{id}` 形式，因此用 Method+模板路径精确匹配。
            operation = operations.get((step.http_method, step.api_path))
            if operation is None:
                issues.append(
                    f"case index {case_index} step {step.step_number} references an API operation "
                    "not found in OpenAPI"
                )
                continue
            if step.operation_id is not None and step.operation_id != operation:
                issues.append(
                    f"case index {case_index} step {step.step_number} operationId does not match "
                    "the OpenAPI operation"
                )

        for expectation in case.unverifiable_expectations:
            # 不确定的业务结论保留为 warning，让使用者知道需要补规则或人工确认。
            message = f"case index {case_index} has an unverifiable expectation"
            warnings.append(message)
            unverifiable.append(expectation)

    if issues:
        # 先收集同一份 suite 的多个问题，再一次性反馈，减少反复修复/重跑。
        raise BusinessRuleValidationError(issues)

    return ValidationReport(
        status="passed_with_warnings" if warnings else "passed",
        case_count=len(suite.test_cases),
        warnings=warnings,
        unverifiable_expectations=unverifiable,
    )


def _index_openapi(
    document: Mapping[str, Any],
) -> dict[tuple[HttpMethod, str], str | None]:
    """把 OpenAPI 的 paths 转成 {(HTTP method, route): operationId} 查询表。"""
    paths = document.get("paths")
    if not isinstance(paths, Mapping):
        raise OpenAPIDocumentError("OpenAPI document must contain a paths object")

    # HttpMethod 是 Literal，直接从其声明值构造白名单，避免本地维护第二份 method 清单。
    supported_methods = set(HttpMethod.__args__)
    operations: dict[tuple[HttpMethod, str], str | None] = {}
    for path, path_item in paths.items():
        if not isinstance(path, str) or not path.startswith("/"):
            raise OpenAPIDocumentError("OpenAPI paths must be absolute route strings")
        if not isinstance(path_item, Mapping):
            raise OpenAPIDocumentError("Each OpenAPI path item must be an object")

        for method, operation in path_item.items():
            normalized_method = str(method).upper()
            # path item 还会包含 parameters、summary 等非 HTTP method 属性，跳过它们。
            if normalized_method not in supported_methods:
                continue
            if not isinstance(operation, Mapping):
                raise OpenAPIDocumentError("Each OpenAPI operation must be an object")
            operation_id = operation.get("operationId")
            if operation_id is not None and not isinstance(operation_id, str):
                raise OpenAPIDocumentError("OpenAPI operationId values must be strings")
            key = (normalized_method, path)
            if key in operations:
                raise OpenAPIDocumentError("OpenAPI document contains a duplicate operation")
            operations[key] = operation_id
    return operations


def _case_fingerprint(case: TestCase) -> str:
    """生成稳定的行为指纹；忽略空格/大小写，避免微小文案差异漏报重复。"""

    def normalize(value: str | None) -> str | None:
        if value is None:
            return None
        return re.sub(r"\s+", " ", value).strip().casefold()

    signature = {
        # 不纳入 case_id/title/source_refs，防止只改标题或编号就绕过重复识别。
        "scenario_type": case.scenario_type,
        "objective": normalize(case.objective),
        "preconditions": sorted(normalize(item) for item in case.preconditions),
        "steps": [
            {
                "action": normalize(step.action),
                "expected_result": normalize(step.expected_result),
                "api_path": normalize(step.api_path),
                "http_method": step.http_method,
                "operation_id": normalize(step.operation_id),
            }
            for step in case.steps
        ],
        "expected_results": sorted(normalize(item) for item in case.expected_results),
    }
    encoded = json.dumps(signature, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    # 用摘要作为集合成员，避免直接长期保存/比较整块重复文本。
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
