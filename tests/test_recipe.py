"""Recipe (YAML config-as-code) parsing, validation and URL filtering."""
from __future__ import annotations

import pytest

from dataforge.cli.recipe import (
    EXAMPLE_RECIPE,
    Recipe,
    RecipeError,
    load_recipe,
    write_example_recipe,
)

MINIMAL = """
name: minimal
source:
  urls:
    - https://example.com/sitemap.xml
"""


def _write(tmp_path, text: str, name: str = "r.yaml"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_minimal_recipe_uses_sensible_defaults(tmp_path):
    r = load_recipe(_write(tmp_path, MINIMAL))
    assert r.name == "minimal"
    assert r.stream is True                      # streaming is the default for recipes
    assert r.generation.format == "qa"
    assert r.generation.n_per_chunk == 3
    assert r.quality.threshold == 0.5
    assert r.export.targets == ["local"]


def test_shipped_example_is_valid(tmp_path):
    r = load_recipe(_write(tmp_path, EXAMPLE_RECIPE))
    assert r.name
    assert r.source.urls
    assert r.generation.goal.strip()


def test_write_example_recipe_roundtrips(tmp_path):
    p = write_example_recipe(tmp_path / "out.yaml")
    assert load_recipe(p).stream is True

    with pytest.raises(RecipeError, match="already exists"):
        write_example_recipe(p)

    write_example_recipe(p, force=True)  # --force overwrites


def test_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(RecipeError, match="not found"):
        load_recipe(tmp_path / "nope.yaml")


def test_malformed_yaml_is_reported_as_yaml(tmp_path):
    with pytest.raises(RecipeError, match="not valid YAML"):
        load_recipe(_write(tmp_path, "name: x\n  bad: [indent\n"))


def test_empty_file_is_rejected(tmp_path):
    with pytest.raises(RecipeError, match="empty"):
        load_recipe(_write(tmp_path, "\n"))


def test_unknown_key_is_caught_with_its_path(tmp_path):
    bad = MINIMAL + "\ngeneration:\n  formt: qa\n"
    with pytest.raises(RecipeError) as exc:
        load_recipe(_write(tmp_path, bad))
    assert "generation.formt" in str(exc.value)
    assert "unknown key" in str(exc.value)


def test_source_requires_urls_or_file(tmp_path):
    with pytest.raises(RecipeError, match="urls.*url_file"):
        load_recipe(_write(tmp_path, "name: x\nsource:\n  include: ['/a']\n"))


def test_custom_format_requires_system_prompt(tmp_path):
    bad = MINIMAL + "\ngeneration:\n  format: custom\n"
    with pytest.raises(RecipeError, match="system_prompt"):
        load_recipe(_write(tmp_path, bad))


def test_huggingface_target_requires_repo_id(tmp_path):
    bad = MINIMAL + "\nexport:\n  targets: [huggingface]\n"
    with pytest.raises(RecipeError, match="hf_repo_id"):
        load_recipe(_write(tmp_path, bad))


def test_kaggle_target_requires_slug(tmp_path):
    bad = MINIMAL + "\nexport:\n  targets: [kaggle]\n"
    with pytest.raises(RecipeError, match="kaggle_slug"):
        load_recipe(_write(tmp_path, bad))


def test_unsupported_version_is_rejected(tmp_path):
    with pytest.raises(RecipeError, match="version"):
        load_recipe(_write(tmp_path, "version: 99\n" + MINIMAL))


def test_threshold_out_of_range(tmp_path):
    bad = MINIMAL + "\nquality:\n  threshold: 1.5\n"
    with pytest.raises(RecipeError, match="threshold"):
        load_recipe(_write(tmp_path, bad))


# -- URL resolution and filtering -------------------------------------------


def test_url_file_is_resolved_relative_to_the_recipe(tmp_path):
    (tmp_path / "urls.txt").write_text(
        "# a comment\nhttps://a.example/1\n\nhttps://a.example/2\n", encoding="utf-8"
    )
    p = _write(tmp_path, "name: x\nsource:\n  url_file: urls.txt\n")
    assert load_recipe(p).resolve_seed_urls(p.parent) == [
        "https://a.example/1",
        "https://a.example/2",
    ]


def test_missing_url_file_is_a_clear_error(tmp_path):
    p = _write(tmp_path, "name: x\nsource:\n  url_file: gone.txt\n")
    with pytest.raises(RecipeError, match="url_file not found"):
        load_recipe(p).resolve_seed_urls(p.parent)


def test_inline_and_file_urls_merge_and_dedupe(tmp_path):
    (tmp_path / "urls.txt").write_text(
        "https://a.example/1\nhttps://a.example/3\n", encoding="utf-8"
    )
    p = _write(
        tmp_path,
        "name: x\nsource:\n  urls: [https://a.example/1, https://a.example/2]\n"
        "  url_file: urls.txt\n",
    )
    assert load_recipe(p).resolve_seed_urls(p.parent) == [
        "https://a.example/1",
        "https://a.example/2",
        "https://a.example/3",
    ]


@pytest.mark.parametrize(
    "extra,expected",
    [
        ("", ["/a/1", "/b/2", "/a/3", "/es/a/4"]),
        ("  include: ['/a/']\n", ["/a/1", "/a/3", "/es/a/4"]),
        ("  exclude: ['/es/']\n", ["/a/1", "/b/2", "/a/3"]),
        ("  include: ['/a/']\n  exclude: ['/es/']\n", ["/a/1", "/a/3"]),
        ("  max_urls: 2\n", ["/a/1", "/b/2"]),
    ],
)
def test_filter_urls(tmp_path, extra, expected):
    r = load_recipe(_write(tmp_path, MINIMAL + extra))
    urls = ["/a/1", "/b/2", "/a/3", "/es/a/4"]
    assert r.filter_urls(urls) == expected


# -- Settings overlay --------------------------------------------------------


def test_apply_to_settings_only_touches_declared_keys(tmp_path):
    from dataforge.config.settings import Settings

    s = Settings(output_dir=tmp_path / "o", db_path=tmp_path / "d.db")
    original_chunk = s.chunk_size

    r = load_recipe(_write(tmp_path, MINIMAL + "\ncrawl:\n  rate_limit: 0.5\n"))
    r.apply_to_settings(s)

    assert s.rate_limit == 0.5
    assert s.chunk_size == original_chunk     # untouched — not declared in the recipe
    assert s.stream_pipeline is True          # mirrors recipe.stream


def test_export_kwargs_match_exporter_signature(tmp_path):
    import inspect

    from dataforge.agents.exporter import ExporterAgent

    r = load_recipe(_write(tmp_path, MINIMAL))
    params = inspect.signature(ExporterAgent.__init__).parameters
    for key in r.export_kwargs():
        assert key in params, f"recipe emits '{key}' which ExporterAgent does not accept"


def test_recipe_model_is_constructible_directly():
    """The schema is usable as a library type, not only via YAML."""
    r = Recipe(name="x", source={"urls": ["https://a.example"]})
    assert r.stream is True
    assert r.data_format().value == "qa"


# -- Language / locale filtering ---------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.ready.gov/floods", ""),
        ("https://www.ready.gov/cybersecurity", ""),
        ("https://www.ready.gov/be-informed", ""),   # hyphenated slug, not a locale
        ("https://www.ready.gov/es", "es"),
        ("https://www.ready.gov/es/terremotos", "es"),
        ("https://www.ready.gov/fr/avalanche", "fr"),
        ("https://www.fema.gov/zh-hans", "zh-hans"),
        ("https://www.fema.gov/ht", "ht"),
        ("https://www.ready.gov/", ""),
    ],
)
def test_url_locale_detection(url, expected):
    from dataforge.cli.recipe import url_locale

    assert url_locale(url) == expected


def test_language_en_keeps_only_root_level_pages(tmp_path):
    r = load_recipe(_write(tmp_path, MINIMAL + "  language: en\n"))
    urls = [
        "https://www.ready.gov/floods",
        "https://www.ready.gov/es/terremotos",
        "https://www.ready.gov/es",
        "https://www.ready.gov/fr/avalanche",
        "https://www.fema.gov/zh-hans",
        "https://www.ready.gov/be-informed",
    ]
    assert r.filter_urls(urls) == [
        "https://www.ready.gov/floods",
        "https://www.ready.gov/be-informed",
    ]


def test_language_can_select_a_translation(tmp_path):
    r = load_recipe(_write(tmp_path, MINIMAL + "  language: es\n"))
    urls = [
        "https://www.ready.gov/floods",
        "https://www.ready.gov/es/terremotos",
        "https://www.ready.gov/fr/avalanche",
    ]
    assert r.filter_urls(urls) == ["https://www.ready.gov/es/terremotos"]


def test_unknown_language_is_rejected(tmp_path):
    with pytest.raises(RecipeError, match="unknown language"):
        load_recipe(_write(tmp_path, MINIMAL + "  language: klingon\n"))


def test_regex_patterns_in_include_and_exclude(tmp_path):
    r = load_recipe(
        _write(tmp_path, MINIMAL + "  exclude: ['re:\\.(pdf|docx)$', 're:-old$']\n")
    )
    urls = [
        "https://x.example/guide",
        "https://x.example/guide.pdf",
        "https://x.example/plan.docx",
        "https://x.example/draft-old",
        "https://x.example/pdf-guidance",   # 'pdf' present but not an extension
    ]
    assert r.filter_urls(urls) == [
        "https://x.example/guide",
        "https://x.example/pdf-guidance",
    ]


def test_invalid_regex_fails_at_load_not_mid_crawl(tmp_path):
    with pytest.raises(RecipeError, match="not a valid regex"):
        load_recipe(_write(tmp_path, MINIMAL + "  exclude: ['re:[unclosed']\n"))


def test_shipped_example_keeps_real_ready_gov_content_pages(tmp_path):
    """Guards the example recipe against the filter bug this replaced.

    An earlier version used include: [/hazard, /plan, /kit] which silently
    dropped every Ready.gov hazard page, because they live at the root.
    """
    r = load_recipe(_write(tmp_path, EXAMPLE_RECIPE))
    keep = [
        "https://www.ready.gov/floods",
        "https://www.ready.gov/earthquakes",
        "https://www.ready.gov/cybersecurity",
        "https://www.ready.gov/be-informed",
        "https://www.ready.gov/business/emergency-plans",
    ]
    drop = [
        "https://www.ready.gov/es/terremotos",
        "https://www.ready.gov/fr/avalanche",
        "https://www.ready.gov/node/1234",
        "https://www.ready.gov/sites/default/files/2024-03/x.pdf",
        "https://www.ready.gov/2026-national-preparedness-month-partner-toolkit",
        "https://www.ready.gov/",
    ]
    kept = r.filter_urls(keep + drop)
    assert kept == keep, f"unexpected filtering: {kept}"
