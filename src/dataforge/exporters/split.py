"""Group-aware train/validation/test splitting.

A synthetic dataset built from scraped pages is NOT independently distributed:
``n_per_chunk`` samples come from one chunk, and several chunks come from one
page, so every sample derived from a page is a paraphrase of the same source
text. Splitting such a dataset with a random shuffle puts near-duplicates of the
same passage in both train and eval, the model answers eval questions from text
it memorised in training, and the reported score is inflated — often badly.

The fix is to split on *groups* rather than samples: every sample tracing back to
one page lands in exactly one split. That is what this module does.

Because group sizes vary (a long page yields more samples than a short one),
exact proportions are not generally achievable. Groups are assigned greedily to
whichever split is furthest below its target share, which keeps the realised
proportions close while never splitting a group.
"""
from __future__ import annotations

import random
from collections import defaultdict

from dataforge.utils import get_logger

log = get_logger("export.split")

# Grouping keys, coarsest first. "page" is the safe default: two chunks of one
# page overlap by design (chunk_overlap), so grouping by chunk still leaks.
GROUP_KEYS = {
    "page": "page_id",
    "url": "source_url",
    "chunk": "chunk_id",
}


class SplitError(Exception):
    """Raised when a split cannot be performed as configured."""


def group_key_for(record: dict, group_by: str) -> str:
    """Stable grouping key for one record, falling back when lineage is absent."""
    if group_by == "none":
        return f"sample:{record.get('id')}"
    field = GROUP_KEYS.get(group_by)
    if field is None:
        raise SplitError(f"unknown group_by {group_by!r} (use: page, url, chunk, none)")
    value = record.get(field)
    if value in (None, "", 0):
        # No lineage on this record — treat it as its own group rather than
        # silently lumping every such record together in one split.
        return f"orphan:{record.get('id')}"
    return f"{field}:{value}"


def split_records(
    records: list[dict],
    *,
    ratios: dict[str, float],
    group_by: str = "page",
    seed: int = 42,
) -> dict[str, list[dict]]:
    """Partition ``records`` into named splits without breaking groups.

    ``ratios`` maps split name to target share (e.g. ``{"train": 0.8,
    "validation": 0.1, "test": 0.1}``). Shares are normalised, so they need not
    sum to exactly 1.0. The split is deterministic for a given ``seed``.
    """
    if not records:
        return {name: [] for name in ratios}

    active = {name: share for name, share in ratios.items() if share > 0}
    if not active:
        raise SplitError("no split has a share greater than zero")

    total_share = sum(active.values())
    targets = {name: share / total_share for name, share in active.items()}

    groups: dict[str, list[dict]] = defaultdict(list)
    for rec in records:
        groups[group_key_for(rec, group_by)].append(rec)

    # Shuffle groups (not samples) deterministically, then place the largest
    # first: big groups constrain the outcome most, so assigning them while all
    # splits are still empty keeps the realised ratios closest to target.
    keys = sorted(groups)
    random.Random(seed).shuffle(keys)
    keys.sort(key=lambda k: len(groups[k]), reverse=True)

    out: dict[str, list[dict]] = {name: [] for name in targets}
    assigned = 0
    for key in keys:
        members = groups[key]
        # Pick the split furthest below its target share so far.
        name = min(
            targets,
            key=lambda n: (len(out[n]) / assigned if assigned else 0.0) - targets[n],
        )
        out[name].extend(members)
        assigned += len(members)

    for name in ratios:
        out.setdefault(name, [])

    _log_summary(out, groups, group_by, len(records))
    return out


def _log_summary(
    out: dict[str, list[dict]],
    groups: dict[str, list[dict]],
    group_by: str,
    total: int,
) -> None:
    parts = [
        f"{name}={len(recs)} ({len(recs) / total * 100:.0f}%)"
        for name, recs in out.items()
        if recs
    ]
    log.info(
        f"Split {total} samples across {len(groups)} group(s) by {group_by}: "
        + ", ".join(parts)
    )


def assert_no_group_leakage(splits: dict[str, list[dict]], group_by: str = "page") -> None:
    """Raise if any group appears in more than one split.

    Cheap enough to run on every export, and it is the one property the whole
    module exists to guarantee.
    """
    seen: dict[str, str] = {}
    for name, records in splits.items():
        for rec in records:
            key = group_key_for(rec, group_by)
            owner = seen.setdefault(key, name)
            if owner != name:
                raise SplitError(
                    f"group {key} appears in both '{owner}' and '{name}' — "
                    "the split leaked and eval scores would be inflated"
                )
