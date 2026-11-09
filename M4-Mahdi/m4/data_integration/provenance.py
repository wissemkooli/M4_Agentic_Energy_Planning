"""Data provenance primitives.

Every dataset that enters M4 carries a `DatasetProvenance` record. Every simulation,
optimization run and strategy carries the list of provenance records it used, so the
API and the frontend can always show whether a number is based on MOCK or REAL inputs.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path


class DataStatus(str, Enum):
    MOCK = "MOCK"        # synthetic or fictional values
    REAL = "REAL"        # produced by the team pipeline / documented sources
    UNKNOWN = "UNKNOWN"  # not declared in the manifest
    MIXED = "MIXED"      # aggregate of MOCK and REAL inputs (reported, never hidden)


MOCK_MARKERS = ("mock", "synthetic", "fictional", "fake", "dummy")


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class DatasetProvenance:
    source: str                    # logical name, e.g. "m1_demand_forecast"
    path: str                      # path relative to data/input
    data_status: DataStatus
    dataset_version: str
    producer: str = ""
    description: str = ""
    sha256: str = ""
    rows: int = 0
    adapter: str = ""
    transformations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["data_status"] = self.data_status.value
        return d


def aggregate_status(statuses: list[DataStatus]) -> DataStatus:
    s = set(statuses)
    if not s:
        return DataStatus.UNKNOWN
    if s == {DataStatus.REAL}:
        return DataStatus.REAL
    if s == {DataStatus.MOCK}:
        return DataStatus.MOCK
    if DataStatus.REAL in s and DataStatus.MOCK in s:
        return DataStatus.MIXED
    return DataStatus.UNKNOWN if DataStatus.REAL not in s else DataStatus.MIXED


def contains_mock_marker(values) -> bool:
    for v in values:
        if isinstance(v, str) and any(m in v.lower() for m in MOCK_MARKERS):
            return True
    return False
