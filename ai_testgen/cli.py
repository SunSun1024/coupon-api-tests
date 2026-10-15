"""命令行入口：从文件读取需求，显式调用生成器并报告产物路径。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from ai_testgen.config import ConfigurationError, LLMSettings
from ai_testgen.generator import GenerationInputError, GenerationRequest, TestCaseGenerator
from ai_testgen.llm_client import (
    JSONParseError,
    LLMGenerationError,
    OpenAICompatibleClient,
    OutputSchemaError,
)
from ai_testgen.validator import BusinessRuleValidationError, OpenAPIDocumentError


DEFAULT_COUPON_OPENAPI = Path(__file__).resolve().parents[1] / "examples/ai_testgen/coupon-openapi.json"


def build_parser() -> argparse.ArgumentParser:
    """集中定义命令和参数，确保 `--help` 不需要创建模型客户端或密钥。"""
    parser = argparse.ArgumentParser(
        prog="python -m ai_testgen",
        description="Generate structured API test case candidates from requirements.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate_parser = subparsers.add_parser(
        "generate", help="generate and validate a candidate test suite"
    )
    generate_parser.add_argument(
        "--requirements", required=True, type=Path, help="business requirements Markdown/text file"
    )
    generate_parser.add_argument(
        "--openapi", type=Path, help="optional OpenAPI JSON file"
    )
    generate_parser.add_argument(
        "--business-rules", type=Path, help="optional business rules text file"
    )
    generate_parser.add_argument(
        "--constraints", type=Path, help="optional test constraints text file"
    )
    generate_parser.add_argument(
        "--focus",
        help=(
            "optional natural-language hint for prioritizing test coverage; "
            "does not change business rules"
        ),
    )
    generate_parser.add_argument(
        "--requirement-version",
        help="optional human-managed requirement version; defaults to the input SHA-256",
    )
    generate_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/ai_testgen"),
        help="directory for raw/ and validated/ JSON files",
    )
    run_parser = subparsers.add_parser(
        "run", help="generate, safely plan, execute locally, and write HTML reports"
    )
    run_parser.add_argument(
        "--requirements", required=True, type=Path, help="business requirements Markdown/text file"
    )
    run_parser.add_argument(
        "--openapi", type=Path, default=DEFAULT_COUPON_OPENAPI,
        help="trusted Coupon API OpenAPI contract (defaults to the repository example)",
    )
    run_parser.add_argument("--business-rules", type=Path, help="optional business rules text file")
    run_parser.add_argument("--constraints", type=Path, help="optional test constraints text file")
    run_parser.add_argument(
        "--focus", help="natural-language coverage focus; it does not change formal business rules"
    )
    run_parser.add_argument(
        "--requirement-version", help="optional human-managed requirement version; defaults to input SHA-256"
    )
    run_parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/ai_testgen"),
        help="directory for generated cases, run manifests, and reports",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """负责串起读取、配置、调用，并把不同失败类型变成清楚的 CLI 提示。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return _run_pipeline_command(args)
    try:
        # 先读取并验证文件，再加载模型配置；输入错时不应创建客户端或发起 API 请求。
        request = _load_request(args)
        settings = LLMSettings.from_env()
        client = OpenAICompatibleClient(settings)
        generator = TestCaseGenerator(
            client,
            output_dir=args.output_dir,
            model_name=settings.model_name,
            max_test_cases=settings.max_test_cases,
            cases_per_request=settings.max_cases_per_request,
        )
        result = generator.generate(request)
    # 分开错误类别便于用户判断问题发生在输入、JSON、Pydantic、业务规则还是模型调用阶段。
    except (ConfigurationError, GenerationInputError, OpenAPIDocumentError, OSError, UnicodeError) as exc:
        print(f"Input/configuration error: {exc}", file=sys.stderr)
        return 2
    except JSONParseError as exc:
        print(f"JSON parse failed: {exc}", file=sys.stderr)
        return 1
    except OutputSchemaError as exc:
        print(f"Output schema validation failed: {exc}", file=sys.stderr)
        return 1
    except BusinessRuleValidationError as exc:
        print(f"Business rule validation failed: {exc}", file=sys.stderr)
        return 1
    except LLMGenerationError as exc:
        print(f"Model generation failed: {exc}", file=sys.stderr)
        return 1

    # 只展示状态、token 和文件位置，不打印需求正文或模型原始回复。
    usage = result.metadata.token_usage
    print(
        f"Generated {result.validation.case_count} case(s); "
        f"validation={result.validation.status}; "
        f"prompt_tokens={usage.prompt_tokens}; "
        f"completion_tokens={usage.completion_tokens}; "
        f"total_tokens={usage.total_tokens}; "
        f"llm_calls={result.metadata.llm_call_count}"
    )
    print(f"raw_output={result.raw_path}")
    print(f"validated_output={result.validated_path}")
    return 0


def _run_pipeline_command(args: argparse.Namespace) -> int:
    """输入检查通过后调用一键流水线；报告路径始终显示给用户。"""
    try:
        request = _load_request(args)
        settings = LLMSettings.from_env()
        from ai_testgen.pipeline import run_pipeline

        result = run_pipeline(request, settings, args.output_dir)
    except (ConfigurationError, GenerationInputError, OpenAPIDocumentError, OSError, UnicodeError) as exc:
        print(f"Input/configuration error: {exc}", file=sys.stderr)
        return 2

    print(
        f"Pipeline {result.status}; cases={len(result.cases)}; "
        f"prompt_tokens={result.total_usage['prompt_tokens']}; "
        f"completion_tokens={result.total_usage['completion_tokens']}; "
        f"total_tokens={result.total_usage['total_tokens']}"
    )
    print(f"summary_html={result.summary_html}")
    print(f"summary_json={result.summary_json}")
    if result.detail_report:
        print(f"pytest_html={result.detail_report}")
    if result.exit_code:
        print("Some stages or test cases failed; see the summary report for details.", file=sys.stderr)
    return result.exit_code


def _load_request(args: argparse.Namespace) -> GenerationRequest:
    """将 CLI 路径参数转换成生成器使用的请求对象。"""
    requirements = _read_text(args.requirements)
    business_rules = _read_text(args.business_rules) if args.business_rules else None
    constraints = _read_text(args.constraints) if args.constraints else None
    api_document = _read_json_object(args.openapi) if args.openapi else None
    return GenerationRequest(
        business_requirements=requirements,
        api_document=api_document,
        business_rules=business_rules,
        constraints=constraints,
        requirement_version=args.requirement_version,
        focus=args.focus,
    )


def _read_text(path: Path) -> str:
    """统一使用 UTF-8 读取 Markdown/文本需求，避免不同系统默认编码不一致。"""
    return path.read_text(encoding="utf-8")


def _read_json_object(path: Path) -> dict[str, object]:
    """读取 OpenAPI JSON，并在调用模型前检查根节点确实是对象。"""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GenerationInputError(
            f"OpenAPI file is not valid JSON (line {exc.lineno}, column {exc.colno})"
        ) from None
    if not isinstance(value, dict):
        raise GenerationInputError("OpenAPI JSON root must be an object")
    return value
