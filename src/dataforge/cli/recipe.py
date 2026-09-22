"""Recipe files — declarative, version-controllable pipeline configuration.

A recipe is a YAML document that captures every answer the interactive wizard
would otherwise prompt for, so a run can be reproduced exactly, reviewed in a
pull request, and executed unattended::

    dataforge run fema.yaml

The schema is a Pydantic model, so a malformed recipe fails fast with a field
path instead of surfacing as a confusing error halfway through a crawl.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from dataforge.storage.models import DataFormat


class RecipeError(Exception):
    """Raised when a recipe file is missing, unparsable, or invalid."""


# Locale codes that appear as the FIRST path segment on multilingual sites.
# Both FEMA and Ready.gov follow this pattern: English lives at the root
# (/floods) and translations are prefixed (/es/inundaciones, /fr/avalanche).
_LOCALE_SEGMENTS = frozenset({
    "ar", "bn", "de", "el", "en", "es", "fa", "fr", "ht", "hi", "hy", "id",
    "it", "ja", "km", "ko", "lo", "ne", "nl", "pl", "pt", "ru", "so", "sw",
    "ta", "tl", "th", "tr", "uk", "ur", "vi", "yi", "zh",
    "pt-br", "pt-pt", "zh-hans", "zh-hant", "zh-cn", "zh-tw", "es-mx",
    "en-us", "en-gb", "fr-ca", "tl-ph",
})


def url_locale(url: str) -> str:
    """Return the locale prefix of a URL, or "" when it has none (English root).

    >>> url_locale("https://www.ready.gov/floods")
    ''
    >>> url_locale("https://www.ready.gov/es/terremotos")
    'es'
    """
    path = urlparse(url).path.strip("/")
    if not path:
        return ""
    first = path.split("/")[0].lower()
    return first if first in _LOCALE_SEGMENTS else ""


# -- Schema ------------------------------------------------------------------


class SourceConfig(BaseModel):
    """Where the content comes from."""

    model_config = {"extra": "forbid"}

    urls: list[str] = Field(default_factory=list, description="Seed URLs or sitemap URLs")
    url_file: str = ""
    include: list[str] = Field(
        default_factory=list,
        description="Only keep discovered URLs containing any of these substrings",
    )
    exclude: list[str] = Field(
        default_factory=list,
        description="Drop discovered URLs containing any of these substrings",
    )
    language: str = Field(
        "",
        description=(
            "Keep only URLs in this language. 'en' keeps root-level pages and "
            "drops locale-prefixed ones (/es/..., /fr/...). Empty = keep all."
        ),
    )
    max_urls: int = Field(0, ge=0, description="Cap URLs after filtering (0 = no cap)")
    ignore_robots: bool = False
    skip_known: bool = Field(
        False, description="Skip URLs already scraped in an earlier session"
    )

    @model_validator(mode="after")
    def _need_a_source(self) -> SourceConfig:
        if not self.urls and not self.url_file:
            raise ValueError("provide at least one of 'urls' or 'url_file'")
        return self

    @model_validator(mode="after")
    def _patterns_compile(self) -> SourceConfig:
        """Fail on a bad regex here rather than silently matching nothing later."""
        for field_name in ("include", "exclude"):
            for pattern in getattr(self, field_name):
                if pattern.startswith("re:"):
                    try:
                        re.compile(pattern[3:])
                    except re.error as exc:
                        raise ValueError(
                            f"{field_name} pattern {pattern!r} is not a valid regex: {exc}"
                        ) from exc
        if self.language and self.language.lower() not in _LOCALE_SEGMENTS:
            raise ValueError(
                f"unknown language {self.language!r} - use an ISO code such as "
                f"'en', 'es' or 'fr'"
            )
        return self


class CrawlConfig(BaseModel):
    """Politeness and crawl-extent knobs (override global settings)."""

    model_config = {"extra": "forbid"}

    rate_limit: float | None = Field(None, gt=0, description="Requests per second per domain")
    max_pages: int | None = Field(None, gt=0)
    max_crawl_pages: int | None = Field(None, gt=0)
    max_crawl_depth: int | None = Field(None, ge=1)


class GenerationConfig(BaseModel):
    """What to generate and with which model."""

    model_config = {"extra": "forbid"}

    format: Literal["qa", "instruction", "conversation", "custom"] = "qa"
    goal: str = Field("", description="Plain-language description of the dataset's purpose")
    n_per_chunk: int = Field(3, ge=1, le=20)
    model: str = Field("", description="Override the configured LLM for generation")
    system_prompt: str = Field("", description="Custom system prompt (required for format: custom)")
    chunk_size: int | None = Field(None, gt=0)
    chunk_overlap: int | None = Field(None, ge=0)

    @model_validator(mode="after")
    def _custom_needs_prompt(self) -> GenerationConfig:
        if self.format == "custom" and not self.system_prompt.strip():
            raise ValueError("format 'custom' requires a non-empty 'system_prompt'")
        return self


class QualityConfig(BaseModel):
    model_config = {"extra": "forbid"}

    threshold: float = Field(0.5, ge=0.0, le=1.0)
    model: str = Field("", description="Override the LLM used for quality review")
    llm_judge: bool = Field(
        True,
        description=(
            "Check every sample against its source chunk with an LLM. Without it "
            "only length heuristics apply, which pass almost any real answer."
        ),
    )
    min_judge_score: int = Field(4, ge=1, le=5, description="Judge score (1-5) needed to keep a sample")


class SplitConfig(BaseModel):
    """Train/validation/test split, grouped so no source page spans splits."""

    model_config = {"extra": "forbid"}

    train: float = Field(0.8, ge=0.0, le=1.0)
    validation: float = Field(0.1, ge=0.0, le=1.0)
    test: float = Field(0.1, ge=0.0, le=1.0)
    group_by: Literal["page", "url", "chunk", "none"] = Field(
        "page",
        description=(
            "Keep all samples from the same source in one split. 'page' is the "
            "safe default; 'none' splits per sample and WILL leak near-duplicates."
        ),
    )
    seed: int = Field(42, description="Shuffle seed - same seed gives the same split")

    @model_validator(mode="after")
    def _shares_are_sane(self) -> SplitConfig:
        total = self.train + self.validation + self.test
        if total <= 0:
            raise ValueError("at least one of train/validation/test must be > 0")
        if abs(total - 1.0) > 0.01:
            raise ValueError(
                f"train + validation + test must sum to 1.0 (got {total:.3f})"
            )
        return self

    def ratios(self) -> dict[str, float]:
        return {"train": self.train, "validation": self.validation, "test": self.test}


class ExportConfig(BaseModel):
    model_config = {"extra": "forbid"}

    targets: list[Literal["local", "huggingface", "kaggle"]] = Field(
        default_factory=lambda: ["local"]
    )
    approved_only: bool = True
    hf_repo_id: str = ""
    hf_private: bool = True
    kaggle_slug: str = ""
    kaggle_title: str = ""
    split: SplitConfig | None = Field(
        None,
        description="Omit for a single dataset file; set to emit train/validation/test",
    )

    @model_validator(mode="after")
    def _targets_need_ids(self) -> ExportConfig:
        if "huggingface" in self.targets and not self.hf_repo_id:
            raise ValueError("export target 'huggingface' requires 'hf_repo_id'")
        if "kaggle" in self.targets and not self.kaggle_slug:
            raise ValueError("export target 'kaggle' requires 'kaggle_slug'")
        return self


class Recipe(BaseModel):
    """A complete, reproducible pipeline definition."""

    model_config = {"extra": "forbid"}

    version: int = Field(1, description="Recipe schema version")
    name: str = Field(..., min_length=1, description="Session name")
    stream: bool = Field(
        True,
        description="Overlap collection/processing/generation (recommended for large sites)",
    )
    output_dir: str = ""

    source: SourceConfig
    crawl: CrawlConfig = Field(default_factory=CrawlConfig)
    generation: GenerationConfig = Field(default_factory=GenerationConfig)
    quality: QualityConfig = Field(default_factory=QualityConfig)
    export: ExportConfig = Field(default_factory=ExportConfig)

    @field_validator("version")
    @classmethod
    def _known_version(cls, v: int) -> int:
        if v != 1:
            raise ValueError(f"unsupported recipe version {v} (this build understands version 1)")
        return v

    # -- Derived helpers --------------------------------------------------

    def resolve_seed_urls(self, base_dir: Path) -> list[str]:
        """Seed URLs from the inline list plus ``url_file``, de-duplicated.

        ``url_file`` is resolved relative to the recipe's own directory so a
        recipe plus its URL list can be committed and moved together.
        """
        urls = list(self.source.urls)
        if self.source.url_file:
            path = Path(self.source.url_file)
            if not path.is_absolute():
                path = base_dir / path
            if not path.exists():
                raise RecipeError(f"source.url_file not found: {path}")
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    urls.append(line)

        seen: set[str] = set()
        ordered: list[str] = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                ordered.append(u)
        if not ordered:
            raise RecipeError("recipe resolved to zero seed URLs")
        return ordered

    def filter_urls(self, urls: list[str]) -> list[str]:
        """Apply language, include, exclude and max_urls to a discovered URL list.

        Order matters: language first (it is the coarsest filter and on a
        multilingual government site removes the largest share), then include,
        then exclude, then the cap — so ``max_urls`` counts only URLs you
        actually want.

        A pattern prefixed with ``re:`` is treated as a regular expression;
        anything else is a plain substring, matching the interactive URL
        review's filter syntax.
        """
        out = urls

        if self.source.language:
            want = self.source.language.lower()
            if want == "en":
                # English is served from the root on both FEMA and Ready.gov.
                out = [u for u in out if not url_locale(u)]
            else:
                out = [u for u in out if url_locale(u) == want]

        if self.source.include:
            out = [u for u in out if any(_matches(p, u) for p in self.source.include)]
        if self.source.exclude:
            out = [u for u in out if not any(_matches(p, u) for p in self.source.exclude)]
        if self.source.max_urls:
            out = out[: self.source.max_urls]
        return out

    def export_kwargs(self) -> dict[str, Any]:
        e = self.export
        return {
            "targets": list(e.targets),
            "approved_only": e.approved_only,
            "hf_repo_id": e.hf_repo_id,
            "hf_private": e.hf_private,
            "kaggle_slug": e.kaggle_slug,
            "kaggle_title": e.kaggle_title,
            "split_ratios": e.split.ratios() if e.split else None,
            "split_group_by": e.split.group_by if e.split else "page",
            "split_seed": e.split.seed if e.split else 42,
        }

    def data_format(self) -> DataFormat:
        return DataFormat(self.generation.format)

    def apply_to_settings(self, s) -> None:
        """Overlay recipe overrides onto a Settings instance, in place.

        Only keys the recipe actually sets are touched, so anything left out
        keeps its value from .env / the global config.
        """
        c, g = self.crawl, self.generation
        for field_name, value in (
            ("rate_limit", c.rate_limit),
            ("max_pages", c.max_pages),
            ("max_crawl_pages", c.max_crawl_pages),
            ("max_crawl_depth", c.max_crawl_depth),
            ("chunk_size", g.chunk_size),
            ("chunk_overlap", g.chunk_overlap),
        ):
            if value is not None:
                setattr(s, field_name, value)

        s.stream_pipeline = self.stream
        if self.output_dir:
            s.output_dir = Path(self.output_dir).expanduser().resolve()


def _matches(pattern: str, url: str) -> bool:
    """Substring match, or regex when the pattern is prefixed with ``re:``."""
    if pattern.startswith("re:"):
        try:
            return re.search(pattern[3:], url) is not None
        except re.error:
            return False
    return pattern in url


# -- Loading -----------------------------------------------------------------


def load_recipe(path: str | Path) -> Recipe:
    """Parse and validate a recipe file.

    Raises :class:`RecipeError` with a readable, line-aware message rather than
    letting a YAML or Pydantic traceback reach the user.
    """
    p = Path(path).expanduser()
    if not p.exists():
        raise RecipeError(f"Recipe file not found: {p}")

    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RecipeError(f"{p} is not valid YAML:\n  {exc}") from exc

    if raw is None:
        raise RecipeError(f"{p} is empty")
    if not isinstance(raw, dict):
        raise RecipeError(f"{p} must contain a YAML mapping at the top level, got {type(raw).__name__}")

    try:
        return Recipe.model_validate(raw)
    except ValidationError as exc:
        raise RecipeError(_format_validation_error(p, exc)) from exc


def _format_validation_error(path: Path, exc: ValidationError) -> str:
    lines = [f"{path} has {exc.error_count()} problem(s):"]
    for err in exc.errors():
        loc = ".".join(str(part) for part in err["loc"]) or "(root)"
        msg = err["msg"]
        if err["type"] == "extra_forbidden":
            msg = "unknown key - check spelling against the example recipe"
        lines.append(f"  {loc}: {msg}")
    return "\n".join(lines)


# -- Scaffolding -------------------------------------------------------------

EXAMPLE_RECIPE = """# DataForge recipe - configuration as code.
#
#   dataforge run this-file.yaml --dry-run   # validate + preview, runs nothing
#   dataforge run this-file.yaml             # execute end-to-end, no prompts
#
# Only `name` and `source.urls` (or `source.url_file`) are required; everything
# else falls back to your .env / global config. Commit this file alongside your
# dataset so the run is reproducible and reviewable.
#
# The filters below were checked against the live sitemaps: Ready.gov publishes
# 371 URLs of which 225 are English, and this recipe keeps ~138 of them.

version: 1
name: ready-gov-preparedness

# Overlap collection, processing and generation. On a large site this is the
# difference between the LLM idling through the whole crawl and it working from
# the first page onward.
stream: true

# output_dir: ./output

source:
  urls:
    - https://www.ready.gov/sitemap.xml

  # ── Language ────────────────────────────────────────────────────────────────
  # Ready.gov and FEMA serve English from the root (/floods, /cybersecurity) and
  # translations under a locale prefix (/es/terremotos, /fr/avalanche). 'en'
  # keeps root-level pages and drops every locale-prefixed one - 146 of
  # Ready.gov's 371 sitemap URLs are Spanish. Use 'es' or 'fr' to invert it.
  language: en

  # ── Scoping ─────────────────────────────────────────────────────────────────
  # NOTE: Ready.gov keeps its hazard content at the ROOT (/floods, /earthquakes,
  # /cybersecurity) - there is no /hazard/ prefix - so an `include` list of path
  # prefixes would filter out exactly the pages you want. Here we take the whole
  # English site and subtract the noise instead.
  #
  # Plain text is a substring match; prefix with 're:' for a regular expression.
  exclude:
    - /node/                     # Drupal internal node ids, duplicates of real pages
    - /sites/default/files/      # file store: PDFs and images, not article content
    - /press-release
    - re:toolkit                 # campaign asset bundles, not prose
    - re:webinar
    - re:social-media
    - re:-old$                   # superseded drafts left in the sitemap
    - /calendar
    - /accessibility
    - /privacy
    - re:^https?://[^/]+/$       # the bare homepage: nav links, no article body

  # Hard cap after filtering. 0 means no cap. Start small to check cost per page
  # before opening it up.
  max_urls: 150

  # Leave robots.txt enforcement on unless you have explicit written permission.
  ignore_robots: false

  # Skip URLs already scraped by an earlier session against the same database.
  skip_known: true

crawl:
  # DataForge automatically slows to a site's robots.txt Crawl-delay when that
  # is stricter than this value. FEMA.gov declares Crawl-delay: 15 (one request
  # every 15 seconds), so a FEMA crawl is paced by that, not by this setting.
  rate_limit: 2.0
  max_pages: 500
  max_crawl_pages: 50          # only used when a site has no reachable sitemap
  max_crawl_depth: 3

generation:
  format: qa                   # qa | instruction | conversation | custom
  goal: >
    Accurate question-and-answer pairs about U.S. disaster preparedness,
    covering hazard-specific guidance, emergency kits and family planning,
    grounded strictly in official Ready.gov and FEMA guidance.
  n_per_chunk: 3
  # model: openai/gpt-4o-mini  # omit to use your configured default
  # system_prompt: "..."       # required only when format: custom
  chunk_size: 512
  chunk_overlap: 64

quality:
  # Cheap length/refusal heuristic, 0.0-1.0. On its own it passes almost any
  # real answer, so treat it as a pre-filter, not the quality bar.
  threshold: 0.5

  # The actual quality bar. An LLM reads each sample next to the chunk it came
  # from and rejects anything not supported by that text, anything that talks
  # about "the document" instead of the subject, or anything scoring below
  # min_judge_score. Costs about one extra call per chunk - roughly the same
  # again as generation. Samples it cannot score are rejected, never passed.
  llm_judge: true
  min_judge_score: 4           # 1-5
  # model: openai/gpt-4o-mini  # judge model; defaults to the generation model

export:
  targets: [local]             # local | huggingface | kaggle (any combination)
  approved_only: true

  # ── Train / validation / test split ─────────────────────────────────────────
  # Omit this block to get one dataset.jsonl and split it yourself later.
  #
  # If you do split, group_by matters more than the ratios. Several samples are
  # generated per chunk and several chunks per page, so every sample from one
  # page is a paraphrase of the same source text. A random per-sample split puts
  # those near-duplicates on both sides, the model answers eval questions from
  # text it saw in training, and the score comes out inflated. 'page' keeps each
  # source page wholly inside one split. Use 'none' only if you know why.
  split:
    train: 0.8
    validation: 0.1
    test: 0.1
    group_by: page             # page | url | chunk | none
    seed: 42                   # same seed -> same split, every run
  # hf_repo_id: your-org/ready-gov-preparedness-qa
  # hf_private: true
  # kaggle_slug: your-user/ready-gov-preparedness-qa
  # kaggle_title: Ready.gov Preparedness QA

# ── Adding FEMA.gov ───────────────────────────────────────────────────────────
# FEMA's sitemap is an INDEX of 133 sub-sitemaps totalling roughly 66,500 URLs,
# and its robots.txt asks for one request every 15 seconds. Crawling it broadly
# is not practical - scope it hard before adding it:
#
#   source:
#     urls:
#       - https://www.ready.gov/sitemap.xml
#       - https://www.fema.gov/sitemap.xml
#     language: en
#     include:
#       - /emergency-managers/individuals-communities
#       - /disaster/how-to-help
#     max_urls: 200
"""



def write_example_recipe(path: str | Path, *, force: bool = False) -> Path:
    """Write the annotated example recipe. Never clobbers without ``force``."""
    p = Path(path).expanduser()
    if p.exists() and not force:
        raise RecipeError(f"{p} already exists — pass --force to overwrite")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(EXAMPLE_RECIPE, encoding="utf-8")
    return p
