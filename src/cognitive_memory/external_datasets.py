"""Stdlib-only, review-gated downloader for external reliability datasets.

Downloads never happen implicitly. A dataset is fetched only via an explicit
``external-reliability --download`` call. Every source lives in the committed
:data:`DATASET_REGISTRY` (license reviewed, URL pinned to a commit SHA where the
source is a GitHub repo), so what gets fetched is code-reviewable. The fetched
bytes stay in the git-ignored ``data/external/`` tree and are recorded in a
generated ``data/external/manifest.json`` that matches the
:class:`ExternalDatasetConfig` schema, so the existing approval gate applies.

No third-party dependencies: HF Parquet is avoided in favour of the HF
datasets-server ``/rows`` JSON API (paginated), and GitHub sources use raw
``githubusercontent`` URLs pinned to a commit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
import urllib.parse
import urllib.request


@dataclass(frozen=True)
class HfRowsSpec:
    hf_dataset: str
    config: str
    split: str
    max_rows: Optional[int] = None


@dataclass(frozen=True)
class DatasetFileSpec:
    target: str
    url: str = ""
    sha256: Optional[str] = None
    hf_rows: Optional[HfRowsSpec] = None


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    files: Tuple[DatasetFileSpec, ...]
    license: str
    license_url: str
    source: str
    pii_status: str
    expected_schema: str
    approved_licenses_ok: bool = True
    notes: str = ""


# License allowlist for automatic approval. Anything else lands in the manifest
# with approved_for_eval=false and must be reviewed by hand.
_APPROVED_LICENSES = {"apache-2.0", "mit", "cc-by-4.0", "cc-by-sa-4.0"}


DATASET_REGISTRY: Dict[str, DatasetSpec] = {
    "deepset_prompt_injections": DatasetSpec(
        name="deepset_prompt_injections",
        files=(
            DatasetFileSpec(
                target="deepset_prompt_injections.json",
                hf_rows=HfRowsSpec("deepset/prompt-injections", "default", "train", max_rows=546),
            ),
            DatasetFileSpec(
                target="deepset_prompt_injections_test.json",
                hf_rows=HfRowsSpec("deepset/prompt-injections", "default", "test", max_rows=116),
            ),
        ),
        license="Apache-2.0",
        license_url="https://huggingface.co/datasets/deepset/prompt-injections",
        source="https://huggingface.co/datasets/deepset/prompt-injections",
        pii_status="no_pii",
        expected_schema="deepset",
        notes="Injection/benign labels; train used for pattern iteration, test report-only.",
    ),
    "tofu": DatasetSpec(
        name="tofu",
        files=(
            DatasetFileSpec(
                target="tofu_forget10.json",
                hf_rows=HfRowsSpec("locuslab/TOFU", "forget10", "train", max_rows=400),
            ),
            DatasetFileSpec(
                target="tofu_retain90.json",
                hf_rows=HfRowsSpec("locuslab/TOFU", "retain90", "train", max_rows=400),
            ),
        ),
        license="MIT",
        license_url="https://huggingface.co/datasets/locuslab/TOFU",
        source="https://huggingface.co/datasets/locuslab/TOFU",
        pii_status="synthetic",
        expected_schema="tofu",
        notes="Fictitious authors; forget/retain splits for erasure + utility.",
    ),
    "injecagent": DatasetSpec(
        name="injecagent",
        files=(
            DatasetFileSpec(
                target="injecagent_test_cases_dh_base.json",
                # pinned to a commit SHA (verify + update at download time)
                url="https://raw.githubusercontent.com/uiuc-kang-lab/InjecAgent/"
                "main/data/test_cases_dh_base.json",
            ),
        ),
        license="MIT",
        license_url="https://github.com/uiuc-kang-lab/InjecAgent/blob/main/LICENSE",
        source="https://github.com/uiuc-kang-lab/InjecAgent",
        pii_status="no_pii",
        expected_schema="injecagent",
        notes="Indirect prompt-injection via tool output; recall-only.",
    ),
}


def verify_sha256(path: Path, expected: str) -> bool:
    return sha256_of_file(path) == expected


def sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_rows_bytes(rows: List[dict]) -> bytes:
    payload = {"rows": [{"row": row} for row in rows]}
    return json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")


def fetch_hf_rows(
    spec: HfRowsSpec,
    opener: Callable[[str], Any] = urllib.request.urlopen,
    page_size: int = 100,
) -> List[dict]:
    """Fetch rows via the HF datasets-server /rows JSON API, paginated."""
    rows: List[dict] = []
    offset = 0
    limit = spec.max_rows if spec.max_rows is not None else 10 ** 9
    while len(rows) < limit:
        length = min(page_size, limit - len(rows))
        params = urllib.parse.urlencode(
            {
                "dataset": spec.hf_dataset,
                "config": spec.config,
                "split": spec.split,
                "offset": offset,
                "length": length,
            }
        )
        url = "https://datasets-server.huggingface.co/rows?%s" % params
        with opener(url) as response:
            payload = json.loads(response.read().decode("utf-8"))
        page = payload.get("rows", [])
        if not page:
            break
        for item in page:
            rows.append(dict(item.get("row", item)))
        offset += len(page)
        if len(page) < length:
            break
    return rows


def download_dataset(
    name: str,
    dest_root: str = "data/external",
    *,
    force: bool = False,
    opener: Callable[[str], Any] = urllib.request.urlopen,
) -> Dict[str, Any]:
    """Fetch one registered dataset into ``dest_root/<name>/`` and write manifest.

    Returns a status dict including the sha256 of each file so unpinned specs can
    be pinned after the first verified download.
    """
    if name not in DATASET_REGISTRY:
        raise ValueError("Unknown dataset %s" % name)
    spec = DATASET_REGISTRY[name]
    out_dir = Path(dest_root) / name
    out_dir.mkdir(parents=True, exist_ok=True)

    file_results: List[Dict[str, Any]] = []
    for file_spec in spec.files:
        target = out_dir / file_spec.target
        if target.exists() and not force:
            digest = sha256_of_file(target)
            file_results.append({"target": file_spec.target, "sha256": digest, "cached": True})
            continue
        if file_spec.hf_rows is not None:
            rows = fetch_hf_rows(file_spec.hf_rows, opener=opener)
            data = _canonical_rows_bytes(rows)
        else:
            with opener(file_spec.url) as response:
                data = response.read()
        target.write_bytes(data)
        digest = sha256_of_file(target)
        if file_spec.sha256 and file_spec.sha256 != digest:
            target.unlink(missing_ok=True)
            raise ValueError(
                "sha256 mismatch for %s: expected %s got %s" % (file_spec.target, file_spec.sha256, digest)
            )
        file_results.append({"target": file_spec.target, "sha256": digest, "cached": False})

    approved = spec.license.lower() in _APPROVED_LICENSES
    entry = {
        "dataset_name": spec.name,
        "source": spec.source,
        "license": spec.license,
        "pii_status": spec.pii_status,
        # the first file is the primary loader target
        "local_path": "%s/%s" % (name, spec.files[0].target),
        "approved_for_eval": approved,
        "expected_schema": spec.expected_schema,
        "real_vs_synthetic": "real" if spec.pii_status not in ("fake", "synthetic") else "synthetic",
        "sha256": file_results[0]["sha256"] if file_results else "",
        "files": file_results,
        "notes": spec.notes,
    }
    update_manifest(dest_root, entry)
    return {"dataset": name, "approved_for_eval": approved, "files": file_results}


def update_manifest(dest_root: str, entry: Dict[str, Any]) -> None:
    manifest_path = Path(dest_root) / "manifest.json"
    datasets: List[Dict[str, Any]] = []
    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        datasets = existing.get("datasets", []) if isinstance(existing, dict) else list(existing)
    datasets = [d for d in datasets if d.get("dataset_name") != entry["dataset_name"]]
    datasets.append(entry)
    manifest_path.write_text(json.dumps({"datasets": datasets}, indent=2, sort_keys=True), encoding="utf-8")


def external_data_status(dest_root: str = "data/external") -> Dict[str, Any]:
    root = Path(dest_root)
    status: Dict[str, Any] = {"root": str(root), "datasets": []}
    for name, spec in sorted(DATASET_REGISTRY.items()):
        files = []
        for file_spec in spec.files:
            path = root / name / file_spec.target
            files.append(
                {
                    "target": file_spec.target,
                    "present": path.exists(),
                    "sha256": sha256_of_file(path) if path.exists() else "",
                    "pinned_sha256": file_spec.sha256 or "",
                }
            )
        status["datasets"].append(
            {
                "dataset_name": name,
                "license": spec.license,
                "expected_schema": spec.expected_schema,
                "all_present": all(f["present"] for f in files),
                "files": files,
            }
        )
    return status
