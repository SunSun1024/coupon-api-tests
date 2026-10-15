import copy
import json

import pytest

from tests.support.ai_case_examples import contract, generate_source, mapping

pytestmark = pytest.mark.regression


@pytest.fixture
def paths(tmp_path):
    source = generate_source(tmp_path / "generated")
    values = {"source": source, "mapping": mapping(source), "openapi": contract()}
    result = {}
    for name, value in values.items():
        result[name] = tmp_path / (name + ".json")
        result[name].write_text(json.dumps(value, ensure_ascii=False))
    result["review"] = tmp_path / "review.json"
    result["output"] = tmp_path / "execution.json"
    return result


def arguments(command, paths):
    return [command, "--source", str(paths["source"]), "--mapping", str(paths["mapping"]),
            "--openapi", str(paths["openapi"])]


def test_explicit_cli_review_then_adapt_and_idempotent_registry(paths, capsys):
    from ai_testexec.cli import main
    assert main(arguments("preview", paths)) == 0
    assert "http_request" in capsys.readouterr().out
    assert main(arguments("review", paths) + ["--reviewer", "human-tester", "--output", str(paths["review"])]) == 0
    receipt = json.loads(paths["review"].read_text())
    assert receipt["reviewer"] == "human-tester"
    args = arguments("adapt", paths) + ["--review", str(paths["review"]), "--output", str(paths["output"])]
    assert main(args) == 0
    first = json.loads(paths["output"].read_text())
    assert main(args) == 0
    assert first == json.loads(paths["output"].read_text())
    assert first["review_id"] == receipt["review_id"]


def test_cli_unreviewed_conversion_writes_reason_without_execution_data(paths):
    from ai_testexec.cli import main
    assert main(arguments("adapt", paths) + ["--output", str(paths["output"])]) == 1
    assert not paths["output"].exists()
    report = json.loads(paths["output"].with_suffix(".conversion.json").read_text())
    assert report["outcomes"][0]["code"] == "review_required"


def test_cli_schema_error_always_writes_conversion_report(paths):
    from ai_testexec.cli import main
    plan = json.loads(paths["mapping"].read_text())
    plan["cases"][0].pop("fixtures")
    paths["mapping"].write_text(json.dumps(plan))
    assert main(arguments("adapt", paths) + ["--output", str(paths["output"])]) == 1
    report = json.loads(paths["output"].with_suffix(".conversion.json").read_text())
    assert report["outcomes"][0]["code"] == "schema_invalid"


def test_registry_rejects_same_program_from_new_generation(paths):
    from ai_testexec.cli import main
    review_args = ["--reviewer", "tester", "--output", str(paths["review"])]
    adapt_args = ["--review", str(paths["review"]), "--output", str(paths["output"])]
    assert main(arguments("review", paths) + review_args) == 0
    assert main(arguments("adapt", paths) + adapt_args) == 0
    source = json.loads(paths["source"].read_text())
    plan = json.loads(paths["mapping"].read_text())
    source["metadata"]["run_id"] += "-regenerated"
    plan["source_run_id"] = source["metadata"]["run_id"]
    paths["source"].write_text(json.dumps(source))
    paths["mapping"].write_text(json.dumps(plan))
    assert main(arguments("review", paths) + review_args) == 0
    paths["output"] = paths["output"].with_name("second-execution.json")
    assert main(arguments("adapt", paths) + ["--review", str(paths["review"]), "--output", str(paths["output"])]) == 1
    assert not paths["output"].exists()
    assert json.loads(paths["output"].with_suffix(".conversion.json").read_text())["outcomes"][0]["code"] == "duplicate_generated"


def test_failed_conversion_invalidates_old_output_but_preserves_backup(paths):
    from ai_testexec.cli import main
    assert main(arguments("review", paths) + ["--reviewer", "tester", "--output", str(paths["review"])]) == 0
    args = arguments("adapt", paths) + ["--review", str(paths["review"]), "--output", str(paths["output"])]
    assert main(args) == 0
    previous = paths["output"].read_text()
    plan = json.loads(paths["mapping"].read_text())
    plan["cases"][0]["operations"][0]["json_body"]["discount_amount_cents"]["value"] = 1000
    paths["mapping"].write_text(json.dumps(plan))
    assert main(args) == 1
    assert not paths["output"].exists(), "失败转换不能留下一个看似最新的旧执行文件"
    report = json.loads(paths["output"].with_suffix(".conversion.json").read_text())
    assert report["outcomes"][0]["code"] == "review_invalid"
    backup = paths["output"].parent / report["invalidated_output"]
    assert backup.read_text() == previous


@pytest.mark.parametrize("collision", ["output_source", "report_mapping", "registry_review", "report_output"])
def test_output_path_collisions_do_not_modify_any_input(paths, collision):
    from ai_testexec.cli import main
    assert main(arguments("review", paths) + ["--reviewer", "tester", "--output", str(paths["review"])]) == 0
    before = {name: p.read_bytes() for name, p in paths.items() if p.exists()}
    flags = {
        "output_source": ["--output", str(paths["source"])],
        "report_mapping": ["--output", str(paths["output"]), "--report", str(paths["mapping"])],
        "registry_review": ["--output", str(paths["output"]), "--registry", str(paths["review"])],
        "report_output": ["--output", str(paths["output"]), "--report", str(paths["output"])],
    }
    assert main(arguments("adapt", paths) + ["--review", str(paths["review"])] + flags[collision]) == 1
    assert all(paths[name].read_bytes() == content for name, content in before.items())
