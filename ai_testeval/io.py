"""Canonical JSON encoding and safe writes for reproducible local artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def json_value(value: Any) -> Any:
    """Convert a Pydantic model into a JSON-compatible value without losing fields."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def canonical_json(value: Any) -> str:
    """Encode stable UTF-8 JSON for content hashes and deterministic artifacts."""
    return json.dumps(
        json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def write_json_atomic(path: str | Path, value: Any) -> Path:
    """Write JSON to a temporary sibling and atomically replace the destination."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(json_value(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent, delete=False
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, destination)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return destination


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_file_digest(path: str | Path, expected: str) -> None:
    actual = file_sha256(path)
    if actual != expected:
        raise ValueError(f"artifact digest mismatch: expected {expected}, got {actual}")
