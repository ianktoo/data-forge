"""evals/pipeline: metrics and the blind audit, on an offline run (no network, no LLM spend)."""
import csv
import json
from pathlib import Path

from dataforge.cli.headless import EXIT_OK, run_recipe
from evals.pipeline import aggregate, audit

from .test_headless_e2e import _recipe, fake_llm, isolated_settings, site  # noqa: F401


async def test_metrics_and_audit_on_offline_run(
    tmp_path, site, isolated_settings, fake_llm, monkeypatch  # noqa: F811
):
    split = {"train": 0.5, "validation": 0.25, "test": 0.25, "group_by": "page", "seed": 42}
    recipe = _recipe(tmp_path, site, export={"targets": ["local"], "split": split})
    assert await run_recipe(recipe) == EXIT_OK

    summary = next(isolated_settings.output_dir.rglob("run_summary.json"))
    entry = {
        "id": "local", "topic": "t", "recipe": recipe, "terms_reviewed": True,
        "preflight": {"robots_status": 200, "crawl_delay": [], "blocked": False},
        "run": {"db_path": str(isolated_settings.db_path), "run_summary": str(summary)},
    }
    m = aggregate.site_metrics(entry["run"])
    entry["metrics"] = m

    assert m["exit_code"] == EXIT_OK
    assert m["pages"] == 4 and m["samples_generated"] > 0
    assert m["samples_approved"] + sum(m["rejection_reasons"].values()) == m["samples_generated"]
    assert m["leak_free"] is True  # group_by: page => no page in two splits
    assert "| local |" in aggregate.markdown([entry])
    assert r"\toprule" in aggregate.latex([entry], {"excluded": []})

    # Blind audit round trip.
    monkeypatch.setattr(audit, "RESULTS", tmp_path)
    (tmp_path / "pipeline_metrics_x.json").write_text(json.dumps({"sites": [entry]}), encoding="utf-8")
    assert audit.sample(per_class=5) == 0
    sheet = tmp_path / "audit_x.csv"
    with sheet.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows and "approved" not in rows[0] and "rejection_reason" not in rows[0]
    for r in rows:
        r["acceptable"] = "y"
    with sheet.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    assert audit.score(sheet) == 0
    scores = json.loads(Path(tmp_path / "audit_x_scores.json").read_text(encoding="utf-8"))
    assert scores["all"]["labelled"] == len(rows)
    if scores["all"]["approved_labelled"]:
        assert scores["all"]["judge_precision"] == 1.0
