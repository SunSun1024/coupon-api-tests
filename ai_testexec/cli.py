"""预览、显式人工审核和转换；CLI 本身不执行 HTTP 请求。"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from uuid import uuid4

from ai_testexec.adapter import CaseAdapter
from ai_testexec.errors import GateError
from ai_testexec.io import read_json, write_json
from ai_testexec.registry import CaseRegistry
from ai_testexec.review import create_review, validate_inputs

DEFAULT_OPENAPI = Path(__file__).resolve().parents[1] / "examples/ai_testgen/coupon-openapi.json"


def validate_output_paths(args, report_path):
    """写文件前检查路径别名，防止输出/报告/SQLite意外覆盖审核输入。"""
    if args.command == "preview":
        return
    inputs = [args.source, args.mapping, args.openapi]
    outputs = [args.output]
    if args.command == "adapt":
        if args.review:
            inputs.append(args.review)
        outputs.extend([report_path, args.registry or args.output.parent / "case-registry.sqlite3"])
    def same(left, right):
        return left.resolve() == right.resolve() or (left.exists() and right.exists() and left.samefile(right))
    if any(same(output, source) for output in outputs for source in inputs) or any(
        same(output, other) for index, output in enumerate(outputs) for other in outputs[index + 1:]
    ):
        raise GateError("output_path_invalid", "输出、报告及去重数据库必须彼此独立，并与输入文件分开")


def invalidate_output(path):
    """转换失败时撤下旧执行文件，备份保留以便追踪；不把旧成功当作本次结果。"""
    if not path.exists():
        return None
    if not path.is_file():
        raise GateError("output_path_invalid", "执行输出路径必须是文件")
    backup = path.with_name(f"{path.stem}.stale-{uuid4().hex}{path.suffix}")
    path.replace(backup)
    return backup.name


def build_parser():
    parser = argparse.ArgumentParser(prog="python -m ai_testexec", description="Review and adapt business cases into restricted execution data.")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("preview", "review", "adapt"):
        sub = commands.add_parser(command)
        sub.add_argument("--source", type=Path, required=True, help="validated ai_testgen JSON artifact")
        sub.add_argument("--mapping", type=Path, required=True, help="explicit execution mapping JSON")
        sub.add_argument("--openapi", type=Path, default=DEFAULT_OPENAPI, help="trusted local OpenAPI contract")
        if command == "review":
            sub.add_argument("--reviewer", required=True, help="human reviewer name")
            sub.add_argument("--acknowledge-warnings", action="store_true")
            sub.add_argument("--output", type=Path, required=True, help="separate human review receipt")
        elif command == "adapt":
            sub.add_argument("--review", type=Path, help="separate human review receipt; omission is refused")
            sub.add_argument("--output", type=Path, required=True, help="derived executable JSON")
            sub.add_argument("--report", type=Path, help="conversion report JSON")
            sub.add_argument("--registry", type=Path, help="persistent dedupe database; defaults beside output")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    report_path = (args.report or args.output.with_suffix(".conversion.json")) if args.command == "adapt" else None
    paths_checked = False
    try:
        validate_output_paths(args, report_path)
        paths_checked = True
        source, mapping, openapi = read_json(args.source), read_json(args.mapping), read_json(args.openapi)
        if args.command == "preview":
            source, mapping, _ = validate_inputs(source, mapping, openapi)
            print(json.dumps({"business_cases": source.test_suite.model_dump(mode="json"),
                              "execution_mapping": mapping.model_dump(mode="json"),
                              "warnings": source.validation.warnings}, ensure_ascii=False, indent=2))
            return 0
        if args.command == "review":
            receipt = create_review(source, mapping, openapi, reviewer=args.reviewer,
                                    acknowledged_warnings=args.acknowledge_warnings)
            write_json(args.output, receipt)
            print(f"review_id={receipt.review_id}; review_output={args.output}")
            return 0
        receipt = read_json(args.review) if args.review else None
        registry = CaseRegistry(args.registry or args.output.parent / "case-registry.sqlite3")
        result = CaseAdapter().adapt(source, mapping, openapi, receipt, seen=registry.snapshot())
        report = {"outcomes": [o.model_dump(mode="json") for o in result.outcomes]}
        if result.bundle is not None:
            registry.record(result.bundle.cases)
            write_json(args.output, result.bundle)
            print(f"execution_output={args.output}; cases={len(result.bundle.cases)}; conversion_report={report_path}")
        else:
            report["invalidated_output"] = invalidate_output(args.output)
            print(f"No executable cases; conversion_report={report_path}", file=sys.stderr)
        write_json(report_path, report)
        return 0 if result.bundle is not None and all(o.status == "ready" for o in result.outcomes) else 1
    except (GateError, OSError, ValueError, TypeError, sqlite3.Error) as error:
        code = error.code if isinstance(error, GateError) else "input_invalid"
        reason = str(error) if isinstance(error, GateError) else "输入文件或记录无法读取/写入"
        if report_path and paths_checked:
            try:
                backup = invalidate_output(args.output)
                write_json(report_path, {"invalidated_output": backup, "outcomes": [
                    {"source_case_id": None, "status": "conversion_failed", "code": code, "reason": reason}
                ]})
            except (GateError, OSError, ValueError):
                print("无法保存转换失败报告，请检查输出目录", file=sys.stderr)
        print(f"{code}: {reason}", file=sys.stderr)
        return 1
