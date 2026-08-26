"""Immutable evaluation dataset manifests and split-leakage validation."""

from __future__ import annotations

import hashlib
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class DatasetTier(StrEnum):
    REGRESSION_FIXTURE = "regression_fixture"
    DEVELOPMENT = "development"
    GOVERNED_RELEASE = "governed_release"


class SplitRole(StrEnum):
    TRAIN = "train"
    DEVELOPMENT = "development"
    TEST = "test"
    LOCKED_HOLDOUT = "locked_holdout"


class SeparationAxis(StrEnum):
    TIME = "time"
    ENTITY = "entity"
    SOURCE = "source"


class ManifestModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DatasetFile(ManifestModel):
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    records: int = Field(ge=0)


class DatasetSplit(ManifestModel):
    split_id: str = Field(min_length=1)
    role: SplitRole
    files: tuple[DatasetFile, ...] = Field(min_length=1)
    time_start: date | None = None
    time_end: date | None = None
    entity_partition: frozenset[str] = frozenset()
    source_partition: frozenset[str] = frozenset()

    @model_validator(mode="after")
    def _time_range_is_complete(self) -> Self:
        if (self.time_start is None) != (self.time_end is None):
            raise ValueError("split time range requires both start and end")
        if self.time_start is not None and self.time_end is not None:
            if self.time_end < self.time_start:
                raise ValueError("split time range ends before it starts")
        return self


class DatasetManifest(ManifestModel):
    dataset_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    tier: DatasetTier
    schema_version: str = Field(min_length=1)
    annotation_guidelines_version: str = Field(min_length=1)
    licence_class: str = Field(min_length=1)
    data_owner: str = Field(min_length=1)
    evaluation_owner: str = Field(min_length=1)
    created_at: AwareDatetime
    document_count: int = Field(default=0, ge=0)
    institution_count: int = Field(default=0, ge=0)
    coverage_start: date | None = None
    coverage_end: date | None = None
    separation_axes: frozenset[SeparationAxis] = frozenset()
    splits: tuple[DatasetSplit, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_release_design(self) -> Self:
        split_ids = [split.split_id for split in self.splits]
        if len(split_ids) != len(set(split_ids)):
            raise ValueError("dataset split IDs must be unique")
        paths = [item.path for split in self.splits for item in split.files]
        if len(paths) != len(set(paths)):
            raise ValueError("a dataset file cannot appear in multiple splits")

        if self.tier is DatasetTier.GOVERNED_RELEASE:
            self._validate_governed_design()

        self._check_separation()
        if self.tier is DatasetTier.GOVERNED_RELEASE:
            self._validate_governed_scale()
        return self

    def _validate_governed_design(self) -> None:
        if self.data_owner == self.evaluation_owner:
            raise ValueError("governed holdout evaluation requires separate ownership")
        if SplitRole.LOCKED_HOLDOUT not in {split.role for split in self.splits}:
            raise ValueError("governed release dataset requires a locked holdout")
        if len(self.splits) < 2:
            raise ValueError("governed release dataset requires at least two splits")
    def _validate_governed_scale(self) -> None:
        required_axes = {
            SeparationAxis.TIME,
            SeparationAxis.ENTITY,
            SeparationAxis.SOURCE,
        }
        if not required_axes <= self.separation_axes:
            raise ValueError("governed release must separate time, entity, and source")
        if self.document_count < 200:
            raise ValueError("governed release requires at least 200 labelled documents")
        if self.institution_count < 30:
            raise ValueError("governed release requires at least 30 institutions")
        if self.coverage_start is None or self.coverage_end is None:
            raise ValueError("governed release requires an explicit coverage interval")
        if (self.coverage_end - self.coverage_start).days < 1_095:
            raise ValueError("governed release requires at least three years of coverage")

    def _check_separation(self) -> None:
        for index, left in enumerate(self.splits):
            for right in self.splits[index + 1 :]:
                if SeparationAxis.ENTITY in self.separation_axes:
                    overlap = left.entity_partition & right.entity_partition
                    if overlap:
                        raise ValueError(f"entity split leakage: {sorted(overlap)}")
                if SeparationAxis.SOURCE in self.separation_axes:
                    overlap = left.source_partition & right.source_partition
                    if overlap:
                        raise ValueError(f"source split leakage: {sorted(overlap)}")
                if SeparationAxis.TIME in self.separation_axes:
                    _check_time_separation(left, right)

    @property
    def production_eligible(self) -> bool:
        return self.tier is DatasetTier.GOVERNED_RELEASE


def _check_time_separation(left: DatasetSplit, right: DatasetSplit) -> None:
    if (
        left.time_start is None
        or left.time_end is None
        or right.time_start is None
        or right.time_end is None
    ):
        raise ValueError("time-separated splits require complete date ranges")
    overlaps = left.time_start <= right.time_end and right.time_start <= left.time_end
    if overlaps:
        raise ValueError(f"time split leakage between {left.split_id!r} and {right.split_id!r}")


def load_and_verify_manifest(path: Path, repository_root: Path) -> DatasetManifest:
    """Load a manifest and verify every referenced byte stream before evaluation."""

    manifest = DatasetManifest.model_validate_json(path.read_text(encoding="utf-8"))
    root = repository_root.resolve()
    for split in manifest.splits:
        for item in split.files:
            candidate = (root / item.path).resolve()
            if not candidate.is_relative_to(root):
                raise ValueError(f"dataset path escapes repository root: {item.path!r}")
            if not candidate.is_file():
                raise ValueError(f"dataset file is missing: {item.path!r}")
            digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
            if digest != item.sha256:
                raise ValueError(f"dataset checksum mismatch: {item.path!r}")
    return manifest


def manifest_digest(path: Path) -> str:
    """Return the immutable identity recorded on governed model releases."""

    manifest = DatasetManifest.model_validate_json(path.read_text(encoding="utf-8"))
    canonical = manifest.model_dump_json(exclude_none=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
