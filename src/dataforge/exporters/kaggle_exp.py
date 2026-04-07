"""Kaggle dataset uploader."""
from __future__ import annotations

import json
import os
from pathlib import Path

from dataforge.utils import get_logger

log = get_logger("export.kaggle")


def push_to_kaggle(
    export_dir: Path,
    dataset_slug: str,    # username/dataset-name
    title: str,
    username: str,
    key: str,
) -> str:
    """Upload a directory as a new Kaggle dataset version. Returns the URL."""
    os.environ["KAGGLE_USERNAME"] = username
    os.environ["KAGGLE_KEY"] = key

    try:
        import kaggle
    except ImportError:
        raise RuntimeError("Install kaggle: uv add kaggle")

    meta_path = export_dir / "dataset-metadata.json"
    meta = {
        "title": title,
        "id": dataset_slug,
        "licenses": [{"name": "CC0-1.0"}],
    }
    meta_path.write_text(json.dumps(meta, indent=2))

    log.info(f"Uploading to Kaggle: {dataset_slug}")
    kaggle.api.authenticate()
    try:
        kaggle.api.dataset_create_new(str(export_dir), public=False, quiet=False)
    except Exception:
        # Dataset exists — create a new version instead
        kaggle.api.dataset_create_version(str(export_dir), version_notes="Updated via DataForge",
                                           quiet=False)

    url = f"https://www.kaggle.com/datasets/{dataset_slug}"
    log.info(f"Kaggle dataset: {url}")
    return url
