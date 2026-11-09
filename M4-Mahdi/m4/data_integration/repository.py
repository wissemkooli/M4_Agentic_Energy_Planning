"""Loads every M4 input through its adapter and caches the result.

The cache key includes the modification time of the manifest and of every input file,
so replacing a CSV (e.g. with the real M1 output) is picked up without restarting.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from ..config import get_settings
from .adapters import (
    BenchmarkData,
    DemandForecastData,
    M1InputAdapter,
    M3InputAdapter,
    ReferenceSystem,
    ReferenceSystemAdapter,
    SourceSpec,
    TechnologyAssumptions,
    TechnologyAssumptionsAdapter,
)
from .load_data import DataLoadError, read_yaml
from .provenance import DataStatus, DatasetProvenance, aggregate_status

REQUIRED_SOURCES = ("m1_demand_forecast", "m3_benchmark", "reference_2024", "technology_assumptions")


@dataclass
class InputBundle:
    demand: DemandForecastData
    benchmark: BenchmarkData
    reference: ReferenceSystem
    assumptions: TechnologyAssumptions

    @property
    def provenance(self) -> list[DatasetProvenance]:
        return [self.demand.provenance, self.benchmark.provenance, self.reference.provenance,
                self.assumptions.provenance]

    @property
    def overall_status(self) -> DataStatus:
        return aggregate_status([p.data_status for p in self.provenance])

    def provenance_dicts(self) -> list[dict]:
        return [p.to_dict() for p in self.provenance]


def load_manifest(manifest_path: Path) -> tuple[dict[str, SourceSpec], Path]:
    data = read_yaml(manifest_path, "Data source manifest")
    input_dir = manifest_path.parent
    missing = [s for s in REQUIRED_SOURCES if s not in data]
    if missing:
        raise DataLoadError(f"sources.yml is missing entries: {', '.join(missing)}")
    return {name: SourceSpec.from_manifest(name, data[name], input_dir) for name in data}, input_dir


def load_inputs(manifest_path: Path | None = None) -> InputBundle:
    manifest_path = manifest_path or get_settings().data_sources_path
    specs, input_dir = load_manifest(manifest_path)
    return InputBundle(
        demand=M1InputAdapter(specs["m1_demand_forecast"], input_dir).load(),
        benchmark=M3InputAdapter(specs["m3_benchmark"], input_dir).load(),
        reference=ReferenceSystemAdapter(specs["reference_2024"], input_dir).load(),
        assumptions=TechnologyAssumptionsAdapter(specs["technology_assumptions"], input_dir).load(),
    )


_lock = threading.Lock()
_cache: dict = {"key": None, "bundle": None}


def _cache_key(manifest_path: Path) -> tuple:
    files = [manifest_path] + [p for p in manifest_path.parent.rglob("*") if p.is_file()]
    return tuple(sorted((str(p), p.stat().st_mtime_ns) for p in files))


def get_inputs() -> InputBundle:
    path = get_settings().data_sources_path
    key = _cache_key(path)
    with _lock:
        if _cache["key"] != key:
            _cache["bundle"] = load_inputs(path)
            _cache["key"] = key
        return _cache["bundle"]
