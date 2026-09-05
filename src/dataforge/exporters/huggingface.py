"""HuggingFace Hub dataset uploader."""
from __future__ import annotations

from pathlib import Path

from dataforge.utils import get_logger

log = get_logger("export.hf")


def push_to_hub(
    jsonl_path: Path,
    repo_id: str,
    token: str,
    *,
    private: bool = True,
    split: str = "train",
    val_ratio: float = 0.1,
) -> str:
    """Upload JSONL as a HuggingFace dataset. Returns the dataset URL."""
    try:
        from datasets import Dataset, DatasetDict
    except ImportError:
        raise RuntimeError("Install huggingface-hub and datasets: uv add huggingface-hub datasets")

    log.info(f"Loading dataset from {jsonl_path}")
    ds = Dataset.from_json(str(jsonl_path))

    if val_ratio > 0 and len(ds) > 10:
        split_ds = ds.train_test_split(test_size=val_ratio, seed=42)
        dataset_dict = DatasetDict({"train": split_ds["train"], "validation": split_ds["test"]})
    else:
        dataset_dict = DatasetDict({"train": ds})

    log.info(f"Pushing to HuggingFace Hub: {repo_id}")
    dataset_dict.push_to_hub(repo_id, token=token, private=private)

    url = f"https://huggingface.co/datasets/{repo_id}"
    log.info(f"Dataset available at {url}")
    return url
