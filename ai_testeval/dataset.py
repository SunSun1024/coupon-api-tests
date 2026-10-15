"""Golden dataset loading, integrity checks, hashing, and human approval."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ai_testeval.io import load_json, sha256_json
from ai_testeval.models import DatasetApproval, GoldenDataset


class DatasetApprovalError(ValueError):
    """An approval is missing, stale, or belongs to a different dataset."""


def load_dataset(path: str | Path) -> GoldenDataset:
    return GoldenDataset.model_validate(load_json(path))


def check_dataset(dataset: GoldenDataset | dict[str, Any]) -> list[str]:
    """Return structural/reference problems; an empty list means the draft is consistent."""
    try:
        GoldenDataset.model_validate(dataset)
    except ValidationError as error:
        return [
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in error.errors(include_url=False)
        ]
    return []


def dataset_digest(dataset: GoldenDataset | dict[str, Any]) -> str:
    validated = GoldenDataset.model_validate(dataset)
    return sha256_json(validated)


def create_approval(
    dataset: GoldenDataset,
    *,
    reviewer: str,
    reviewed_at: datetime | None = None,
) -> DatasetApproval:
    if not reviewer.strip():
        raise ValueError("reviewer must identify the human who reviewed the dataset")
    validated = GoldenDataset.model_validate(dataset)
    return DatasetApproval(
        dataset_id=validated.dataset_id,
        dataset_version=validated.dataset_version,
        dataset_sha256=dataset_digest(validated),
        reviewer=reviewer.strip(),
        reviewed_at=reviewed_at or datetime.now(timezone.utc),
        decision="approved",
    )


def verify_approval(
    dataset: GoldenDataset | dict[str, Any],
    approval: DatasetApproval | dict[str, Any],
) -> DatasetApproval:
    validated_dataset = GoldenDataset.model_validate(dataset)
    validated_approval = DatasetApproval.model_validate(approval)
    if validated_approval.dataset_id != validated_dataset.dataset_id:
        raise DatasetApprovalError("approval dataset_id does not match the dataset")
    if validated_approval.dataset_version != validated_dataset.dataset_version:
        raise DatasetApprovalError("approval dataset_version does not match the dataset")
    if validated_approval.dataset_sha256 != dataset_digest(validated_dataset):
        raise DatasetApprovalError("approval digest does not match the current dataset content")
    return validated_approval
