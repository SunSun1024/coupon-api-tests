"""拒绝执行也保留输入关联；观察到的标识永远不能替代审核授权。"""

from ai_testexec.io import digest, json_data


def observed_trace(bundle, source_case_id=None, *, openapi=None, input_path=None):
    trace = dict(generation_run_id=None, source_case_id=source_case_id, execution_case_id=None,
                 source_digest=None, mapping_digest=None, contract_digest=None, review_id=None,
                 automated_permit_id=None,
                 input_digest=None, input_path=str(input_path) if input_path else None, trace_verified=False)

    def safe_digest(value):
        try:
            return digest(value) if value is not None else None
        except (ValueError, TypeError, RecursionError):
            return None

    data = json_data(bundle)
    trace["input_digest"] = safe_digest(data)
    trace["contract_digest"] = safe_digest(openapi)
    if not isinstance(data, dict):
        return trace
    source = data.get("source")
    trace["source_digest"] = safe_digest(source)
    trace["mapping_digest"] = safe_digest(data.get("mapping"))
    metadata = source.get("metadata") if isinstance(source, dict) else None
    if isinstance(metadata, dict) and isinstance(metadata.get("run_id"), str):
        trace["generation_run_id"] = metadata["run_id"]
    if isinstance(data.get("review_id"), str):
        trace["review_id"] = data["review_id"]
    if isinstance(data.get("automated_permit_id"), str):
        trace["automated_permit_id"] = data["automated_permit_id"]
    cases = data.get("cases", [])
    if isinstance(cases, list):
        for case in cases:
            if isinstance(case, dict) and (
                case.get("source_case_id") == source_case_id or (source_case_id is None and len(cases) == 1)
            ):
                for key in ("source_case_id", "execution_case_id"):
                    if isinstance(case.get(key), str):
                        trace[key] = case[key]
                break
    return trace
