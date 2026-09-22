"""Group-aware splitting — the property that matters is no group leakage."""
from __future__ import annotations

import pytest

from dataforge.exporters.split import (
    SplitError,
    assert_no_group_leakage,
    group_key_for,
    split_records,
)

RATIOS = {"train": 0.8, "validation": 0.1, "test": 0.1}


def _records(n_pages: int, chunks_per_page: int = 3, per_chunk: int = 3) -> list[dict]:
    """Mirror the real shape: many samples per chunk, many chunks per page."""
    out, sid, cid = [], 0, 0
    for page in range(1, n_pages + 1):
        for ci in range(chunks_per_page):
            cid += 1
            for _ in range(per_chunk):
                sid += 1
                out.append({
                    "id": sid,
                    "chunk_id": cid,
                    "page_id": page,
                    "chunk_index": ci,
                    "source_url": f"https://ready.gov/page-{page}",
                    "messages": [{"role": "user", "content": f"q{sid}"}],
                })
    return out


# -- The core guarantee ------------------------------------------------------


def test_no_page_appears_in_two_splits():
    splits = split_records(_records(40), ratios=RATIOS, group_by="page")
    assert_no_group_leakage(splits, "page")

    owners: dict[int, str] = {}
    for name, recs in splits.items():
        for r in recs:
            assert owners.setdefault(r["page_id"], name) == name


def test_grouping_by_url_also_holds():
    splits = split_records(_records(30), ratios=RATIOS, group_by="url")
    assert_no_group_leakage(splits, "url")


def test_leakage_detector_actually_catches_a_bad_split():
    """Guard the guard: a deliberately leaked split must be rejected."""
    recs = _records(4)
    bad = {"train": recs[:-1], "test": recs[-1:]}   # same page on both sides
    with pytest.raises(SplitError, match="leaked"):
        assert_no_group_leakage(bad, "page")


def test_random_sample_level_split_would_leak():
    """Documents why group_by defaults to 'page' rather than 'none'."""
    recs = _records(5)
    splits = split_records(recs, ratios=RATIOS, group_by="none")
    # Splitting per sample puts pages on both sides — exactly the failure mode.
    with pytest.raises(SplitError, match="leaked"):
        assert_no_group_leakage(splits, "page")


# -- Proportions and determinism ---------------------------------------------


def test_every_record_lands_in_exactly_one_split():
    recs = _records(50)
    splits = split_records(recs, ratios=RATIOS, group_by="page")
    ids = [r["id"] for recs_ in splits.values() for r in recs_]
    assert sorted(ids) == sorted(r["id"] for r in recs)
    assert len(ids) == len(set(ids))


def test_proportions_are_close_to_target():
    recs = _records(200)
    splits = split_records(recs, ratios=RATIOS, group_by="page")
    total = len(recs)
    for name, want in RATIOS.items():
        got = len(splits[name]) / total
        assert abs(got - want) < 0.05, f"{name}: wanted ~{want}, got {got:.3f}"


def test_split_is_deterministic_for_a_seed():
    recs = _records(40)
    a = split_records(recs, ratios=RATIOS, group_by="page", seed=7)
    b = split_records(recs, ratios=RATIOS, group_by="page", seed=7)
    assert {k: [r["id"] for r in v] for k, v in a.items()} == {
        k: [r["id"] for r in v] for k, v in b.items()
    }


def test_different_seeds_give_different_splits():
    recs = _records(60)
    a = split_records(recs, ratios=RATIOS, group_by="page", seed=1)
    b = split_records(recs, ratios=RATIOS, group_by="page", seed=2)
    assert [r["id"] for r in a["train"]] != [r["id"] for r in b["train"]]


# -- Edge cases --------------------------------------------------------------


def test_empty_input_yields_empty_splits():
    assert split_records([], ratios=RATIOS) == {"train": [], "validation": [], "test": []}


def test_train_only_ratio_puts_everything_in_train():
    recs = _records(10)
    splits = split_records(recs, ratios={"train": 1.0, "validation": 0.0, "test": 0.0})
    assert len(splits["train"]) == len(recs)
    assert splits["validation"] == [] and splits["test"] == []


def test_all_zero_ratios_is_an_error():
    with pytest.raises(SplitError, match="greater than zero"):
        split_records(_records(3), ratios={"train": 0.0, "test": 0.0})


def test_unknown_group_by_is_an_error():
    with pytest.raises(SplitError, match="unknown group_by"):
        group_key_for({"id": 1}, "banana")


def test_records_without_lineage_become_their_own_groups():
    """Missing page_id must not lump every such record into one split."""
    recs = [{"id": i, "page_id": 0, "source_url": "", "chunk_id": 0} for i in range(20)]
    splits = split_records(recs, ratios=RATIOS, group_by="page")
    assert len(splits["train"]) < len(recs), "orphans were treated as one group"
    assert sum(len(v) for v in splits.values()) == len(recs)


def test_single_page_cannot_be_spread_and_says_so():
    """One group cannot satisfy three splits — it must stay whole, not be cut."""
    recs = _records(1)
    splits = split_records(recs, ratios=RATIOS, group_by="page")
    assert_no_group_leakage(splits, "page")
    non_empty = [n for n, v in splits.items() if v]
    assert len(non_empty) == 1


# -- Recipe integration ------------------------------------------------------


def test_recipe_split_config_reaches_the_exporter(tmp_path):
    import inspect

    from dataforge.agents.exporter import ExporterAgent
    from dataforge.cli.recipe import load_recipe

    p = tmp_path / "r.yaml"
    p.write_text(
        "name: x\nsource:\n  urls: [https://a.example]\n"
        "export:\n  split:\n    train: 0.7\n    validation: 0.15\n    test: 0.15\n"
        "    group_by: page\n    seed: 99\n",
        encoding="utf-8",
    )
    kwargs = load_recipe(p).export_kwargs()
    assert kwargs["split_ratios"] == {"train": 0.7, "validation": 0.15, "test": 0.15}
    assert kwargs["split_seed"] == 99

    params = inspect.signature(ExporterAgent.__init__).parameters
    for key in kwargs:
        assert key in params, f"recipe emits '{key}' the exporter does not accept"


def test_recipe_without_split_keeps_single_file_behaviour(tmp_path):
    from dataforge.cli.recipe import load_recipe

    p = tmp_path / "r.yaml"
    p.write_text("name: x\nsource:\n  urls: [https://a.example]\n", encoding="utf-8")
    assert load_recipe(p).export_kwargs()["split_ratios"] is None


def test_ratios_must_sum_to_one(tmp_path):
    from dataforge.cli.recipe import RecipeError, load_recipe

    p = tmp_path / "r.yaml"
    p.write_text(
        "name: x\nsource:\n  urls: [https://a.example]\n"
        "export:\n  split:\n    train: 0.8\n    validation: 0.8\n    test: 0.1\n",
        encoding="utf-8",
    )
    with pytest.raises(RecipeError, match="sum to 1.0"):
        load_recipe(p)


def test_empty_split_is_warned_not_silent(monkeypatch):
    """Two pages cannot fill three splits; say so instead of writing no file."""
    from unittest.mock import MagicMock

    from dataforge.exporters import split as split_mod

    log = MagicMock()
    monkeypatch.setattr(split_mod, "log", log)
    splits = split_records(_records(2), ratios=RATIOS, group_by="page")
    assert sum(1 for recs in splits.values() if not recs) >= 1
    assert log.warning.called and "received no page group" in log.warning.call_args[0][0]


def test_assignment_is_largest_first_seed_only_breaks_ties():
    """Documented behaviour: held-out splits get pages by size rank, not at random."""
    recs = []
    for page, size in enumerate([30, 20, 10, 5, 5, 5, 5, 5, 5, 5]):
        recs += [{"page_id": page, "chunk_id": page * 100, "source_url": f"https://x.test/{page}"}] * size
    a = split_records(recs, ratios=RATIOS, group_by="page", seed=1)
    b = split_records(recs, ratios=RATIOS, group_by="page", seed=2)
    pages = lambda s: {r["page_id"] for r in s}  # noqa: E731
    # The three largest pages land in the same splits whatever the seed.
    for p in (0, 1, 2):
        assert [n for n in a if p in pages(a[n])] == [n for n in b if p in pages(b[n])]
