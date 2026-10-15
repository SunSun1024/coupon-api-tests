"""Write a self-contained, escaped overview for every AI test pipeline run."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from ai_testexec.io import write_json


def write_summary_report(result: Any, directory: str | Path) -> tuple[Path, Path]:
    """Persist the machine-readable run manifest and a browser-friendly summary."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = result.to_dict() if callable(getattr(result, "to_dict", None)) else dict(result)
    json_path = directory / "pipeline.json"
    html_path = directory / "summary.html"
    write_json(json_path, data)
    html_path.write_text(_render(data), encoding="utf-8")
    return json_path, html_path


def _render(data: dict[str, Any]) -> str:
    esc = lambda value: html.escape(str(value if value is not None else ""), quote=True)
    phases = data.get("phases", {})
    stage_usage = data.get("usage_by_stage", {})
    phase_rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            esc(name), esc(phase.get("status", "unknown")), esc(phase.get("message", ""))
        )
        for name, phase in phases.items()
    ) or '<tr><td colspan="3">No model stages completed</td></tr>'
    usage_rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            esc(name), esc(usage.get("prompt_tokens", 0)),
            esc(usage.get("completion_tokens", 0)), esc(usage.get("total_tokens", 0)),
        )
        for name, usage in stage_usage.items()
    ) or '<tr><td colspan="4">No model token usage recorded</td></tr>'

    cases = data.get("cases", [])
    case_rows = "".join(
        "<tr><td>{}</td><td>{}</td><td><code>{}</code></td><td>{}</td></tr>".format(
            esc(case.get("status", "unknown")),
            esc(case.get("source_case_id", "")),
            esc(case.get("title", "")),
            esc(case.get("reason", "")),
        )
        for case in cases
    ) or '<tr><td colspan="4">No generated test cases</td></tr>'

    usage = data.get("total_usage", {})
    detail = data.get("artifacts", {}).get("detail_report")
    detail_exists = bool(detail and Path(detail).is_file())
    detail_link = '<p><a href="test-report.html">Open detailed Pytest HTML report</a></p>' if detail_exists else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>AI test run {esc(data.get('run_id'))}</title>
<style>body{{font:16px system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#20242a}}
table{{border-collapse:collapse;width:100%;margin:1rem 0 2rem}}th,td{{border:1px solid #d7dce1;padding:.55rem;text-align:left;vertical-align:top}}
th{{background:#f3f5f7}}code{{overflow-wrap:anywhere}}.status{{font-weight:700}}</style></head>
<body><h1>AI test run</h1><p>Run: <code>{esc(data.get('run_id'))}</code> · Model: <code>{esc(data.get('model_name'))}</code>
· Status: <span class="status">{esc(data.get('status'))}</span></p>
<p>Tokens: prompt {esc(usage.get('prompt_tokens', 0))}, completion {esc(usage.get('completion_tokens', 0))},
total {esc(usage.get('total_tokens', 0))}</p>{detail_link}
<h2>Model stages</h2><table><thead><tr><th>Stage</th><th>Status</th><th>Details</th></tr></thead><tbody>{phase_rows}</tbody></table>
<h2>Token usage by stage</h2><table><thead><tr><th>Stage</th><th>Prompt</th><th>Completion</th><th>Total</th></tr></thead><tbody>{usage_rows}</tbody></table>
<h2>Generated test cases</h2><table><thead><tr><th>Status</th><th>Case ID</th><th>Title</th><th>Details</th></tr></thead><tbody>{case_rows}</tbody></table>
</body></html>"""
