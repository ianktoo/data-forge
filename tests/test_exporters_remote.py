"""HuggingFace Hub and Kaggle exporters — no network calls, SDK client mocked.

Covers issue #11: these two export targets had no dedicated test coverage,
only local export was exercised by the e2e test. The required-field
validation path (hf_repo_id/kaggle_slug required when the target is
selected) is a Recipe/pydantic concern, already covered in test_recipe.py
(test_huggingface_target_requires_repo_id, test_kaggle_target_requires_slug)
— this file covers what happens once those fields ARE present, i.e. the
actual upload call shape.
"""
from __future__ import annotations

import json

import pytest

from dataforge.exporters.huggingface import push_to_hub
from dataforge.exporters.kaggle_exp import push_to_kaggle

# -- HuggingFace Hub -------------------------------------------------------------


class _FakeDataset:
    def __init__(self, n: int) -> None:
        self._n = n

    def __len__(self) -> int:
        return self._n

    def train_test_split(self, test_size, seed):
        return {"train": f"train-of-{self._n}", "test": f"val-of-{self._n}"}


class _FakeDatasetDict(dict):
    pushed: dict | None = None

    def push_to_hub(self, repo_id, token, private):
        _FakeDatasetDict.pushed = {
            "repo_id": repo_id, "token": token, "private": private, "splits": dict(self),
        }


@pytest.fixture
def jsonl_file(tmp_path):
    p = tmp_path / "dataset.jsonl"
    p.write_text("\n".join(json.dumps({"messages": []}) for _ in range(3)) + "\n")
    return p


def test_push_to_hub_small_dataset_skips_validation_split(monkeypatch, jsonl_file):
    """<=10 rows: val_ratio split is skipped (train_test_split on a tiny
    dataset is not meaningful), so DatasetDict must only contain 'train'."""
    monkeypatch.setattr("datasets.Dataset.from_json", staticmethod(lambda path: _FakeDataset(3)))
    monkeypatch.setattr("datasets.DatasetDict", _FakeDatasetDict)

    url = push_to_hub(jsonl_file, "someorg/some-dataset", "hf_faketoken", private=True)

    assert url == "https://huggingface.co/datasets/someorg/some-dataset"
    assert _FakeDatasetDict.pushed["repo_id"] == "someorg/some-dataset"
    assert _FakeDatasetDict.pushed["token"] == "hf_faketoken"
    assert _FakeDatasetDict.pushed["private"] is True
    assert set(_FakeDatasetDict.pushed["splits"].keys()) == {"train"}


def test_push_to_hub_larger_dataset_creates_validation_split(monkeypatch, jsonl_file):
    monkeypatch.setattr("datasets.Dataset.from_json", staticmethod(lambda path: _FakeDataset(50)))
    monkeypatch.setattr("datasets.DatasetDict", _FakeDatasetDict)

    push_to_hub(jsonl_file, "someorg/some-dataset", "hf_faketoken", private=False, val_ratio=0.1)

    assert set(_FakeDatasetDict.pushed["splits"].keys()) == {"train", "validation"}
    assert _FakeDatasetDict.pushed["private"] is False


def test_push_to_hub_respects_repo_id_and_token(monkeypatch, jsonl_file):
    monkeypatch.setattr("datasets.Dataset.from_json", staticmethod(lambda path: _FakeDataset(3)))
    monkeypatch.setattr("datasets.DatasetDict", _FakeDatasetDict)

    push_to_hub(jsonl_file, "my-org/my-repo", "hf_secret", private=True)

    assert _FakeDatasetDict.pushed["repo_id"] == "my-org/my-repo"
    assert _FakeDatasetDict.pushed["token"] == "hf_secret"


# -- Kaggle ------------------------------------------------------------------


class _FakeKaggleAPI:
    authenticated = False
    created: list[tuple] = []
    versioned: list[tuple] = []
    fail_create = False

    def authenticate(self):
        type(self).authenticated = True

    def dataset_create_new(self, path, public, quiet):
        if type(self).fail_create:
            raise RuntimeError("dataset already exists")
        type(self).created.append((path, public, quiet))

    def dataset_create_version(self, path, version_notes, quiet):
        type(self).versioned.append((path, version_notes, quiet))


@pytest.fixture
def fake_kaggle(monkeypatch):
    import sys
    import types

    api = _FakeKaggleAPI()
    _FakeKaggleAPI.created = []
    _FakeKaggleAPI.versioned = []
    _FakeKaggleAPI.fail_create = False
    fake_module = types.SimpleNamespace(api=api)
    monkeypatch.setitem(sys.modules, "kaggle", fake_module)
    return api


def test_push_to_kaggle_writes_metadata_and_creates_new_dataset(monkeypatch, tmp_path, fake_kaggle):
    export_dir = tmp_path / "export"
    export_dir.mkdir()

    url = push_to_kaggle(export_dir, "someuser/my-dataset", "My Dataset", "someuser", "fakekey")

    assert url == "https://www.kaggle.com/datasets/someuser/my-dataset"
    assert fake_kaggle.authenticated is True
    assert _FakeKaggleAPI.created == [(str(export_dir), False, False)]
    assert _FakeKaggleAPI.versioned == []

    meta = json.loads((export_dir / "dataset-metadata.json").read_text())
    assert meta["id"] == "someuser/my-dataset"
    assert meta["title"] == "My Dataset"


def test_push_to_kaggle_falls_back_to_new_version_when_dataset_exists(monkeypatch, tmp_path, fake_kaggle):
    _FakeKaggleAPI.fail_create = True
    export_dir = tmp_path / "export"
    export_dir.mkdir()

    push_to_kaggle(export_dir, "someuser/my-dataset", "My Dataset", "someuser", "fakekey")

    assert _FakeKaggleAPI.created == []
    assert len(_FakeKaggleAPI.versioned) == 1
    assert _FakeKaggleAPI.versioned[0][0] == str(export_dir)


def test_push_to_kaggle_sets_credentials_from_arguments(monkeypatch, tmp_path, fake_kaggle):
    import os
    export_dir = tmp_path / "export"
    export_dir.mkdir()

    push_to_kaggle(export_dir, "someuser/my-dataset", "My Dataset", "explicit-user", "explicit-key")

    assert os.environ["KAGGLE_USERNAME"] == "explicit-user"
    assert os.environ["KAGGLE_KEY"] == "explicit-key"
