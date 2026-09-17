"""ExporterAgent — package dataset and upload to configured targets."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import select

from dataforge.cli.preflight import check_export_target
from dataforge.exporters.local import export_all_formats
from dataforge.storage import (
    ExportRecord,
    ProcessedChunk,
    ScrapedPage,
    SyntheticSample,
    open_session,
)
from dataforge.utils.errors import show_warning

from .base import BaseAgent, PipelineContext


class ExporterAgent(BaseAgent):
    name = "exporter"

    def __init__(
        self,
        context: PipelineContext,
        *,
        stage_snapshot: str = "quality",
        approved_only: bool = True,
        targets: list[str] | None = None,        # ["local", "huggingface", "kaggle"]
        hf_repo_id: str = "",
        hf_private: bool = True,
        kaggle_slug: str = "",
        kaggle_title: str = "",
        split_ratios: dict[str, float] | None = None,
        split_group_by: str = "page",
        split_seed: int = 42,
    ) -> None:
        super().__init__(context)
        self._stage = stage_snapshot
        self._approved_only = approved_only
        self._targets = targets or ["local"]
        self._hf_repo  = hf_repo_id
        self._hf_priv  = hf_private
        self._kg_slug  = kaggle_slug
        self._kg_title = kaggle_title
        self._split_ratios   = split_ratios
        self._split_group_by = split_group_by
        self._split_seed     = split_seed

    async def run(self) -> PipelineContext:
        s = self.ctx.settings
        records = self._load_samples()
        if not records:
            self.log.warning("No samples to export")
            return self.ctx

        export_dir = self.ctx.session_dir() / "exports" / datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        self.log.info(f"Exporting {len(records)} samples → {export_dir}")

        if self._split_ratios:
            paths = self._export_splits(records, export_dir)
        else:
            paths = export_all_formats(
                records, export_dir,
                name="dataset",
                include_unsloth=True,
                system_prompt=self.ctx.custom_system_prompt,
            )

        self._record_export("local", str(export_dir), "jsonl+parquet+csv", len(records))

        if "huggingface" in self._targets:
            pf = check_export_target("huggingface")
            if pf.ok and self._hf_repo:
                try:
                    from dataforge.exporters.huggingface import push_to_hub
                    url = push_to_hub(paths["jsonl"], self._hf_repo, s.huggingface_token,
                                      private=self._hf_priv)
                    self._record_export("huggingface", url, "datasets", len(records))
                except Exception as exc:
                    show_warning(f"HuggingFace upload failed: {exc}",
                                 "Data was saved locally. Re-run: dataforge export <session-id>")
            elif not self._hf_repo:
                show_warning("No HuggingFace repo ID specified — skipping HF upload.")

        if "kaggle" in self._targets:
            pf = check_export_target("kaggle")
            if pf.ok and self._kg_slug:
                try:
                    from dataforge.exporters.kaggle_exp import push_to_kaggle
                    url = push_to_kaggle(export_dir, self._kg_slug, self._kg_title,
                                         s.kaggle_username, s.kaggle_key)
                    self._record_export("kaggle", url, "dataset", len(records))
                except Exception as exc:
                    show_warning(f"Kaggle upload failed: {exc}",
                                 "Data was saved locally. Re-run: dataforge export <session-id>")
            elif not self._kg_slug:
                show_warning("No Kaggle slug specified — skipping Kaggle upload.")

        self.log.info("Export complete")
        return self.ctx

    def _export_splits(self, records: list[dict], export_dir) -> dict:
        """Write dataset_train / _validation / _test, grouped to prevent leakage."""
        from dataforge.exporters.split import assert_no_group_leakage, split_records

        splits = split_records(
            records,
            ratios=self._split_ratios or {},
            group_by=self._split_group_by,
            seed=self._split_seed,
        )
        # The guarantee this feature exists for — verify, do not assume.
        assert_no_group_leakage(splits, self._split_group_by)

        paths: dict = {}
        for name, subset in splits.items():
            if not subset:
                self.log.warning(f"Split '{name}' is empty — too few source groups")
                continue
            sub = export_all_formats(
                subset, export_dir,
                name=f"dataset_{name}",
                include_unsloth=True,
                system_prompt=self.ctx.custom_system_prompt,
            )
            paths.update({f"{name}_{k}": v for k, v in sub.items()})
            self.log.info(f"  {name}: {len(subset)} samples")

        # HuggingFace upload pushes a single file; give it the train split.
        paths.setdefault("jsonl", paths.get("train_jsonl", export_dir / "dataset_train.jsonl"))
        return paths

    def _load_samples(self) -> list[dict]:
        """Load approved samples with their source lineage attached.

        ``page_id`` and ``source_url`` are exported deliberately: several
        samples are generated per chunk and several chunks per page, so every
        sample from one page is a paraphrase of the same source text. Splitting
        such a dataset randomly leaks that text across train and eval and
        inflates the score. Grouping by page is the only correct split, and it
        is impossible once this lineage is dropped.
        """
        with open_session(self.ctx.settings.db_path) as db:
            q = select(SyntheticSample).where(
                SyntheticSample.session_id == self.ctx.session_id
            )
            if self._approved_only:
                q = q.where(SyntheticSample.approved == True)  # noqa: E712
            samples = db.exec(q).all()

            chunk_ids = {s.chunk_id for s in samples if s.chunk_id is not None}
            lineage: dict[int, tuple[int, str, int]] = {}
            if chunk_ids:
                chunks = db.exec(
                    select(ProcessedChunk).where(ProcessedChunk.id.in_(chunk_ids))  # type: ignore[union-attr]
                ).all()
                page_ids = {c.page_id for c in chunks}
                page_urls = {
                    p.id: p.url
                    for p in db.exec(
                        select(ScrapedPage).where(ScrapedPage.id.in_(page_ids))  # type: ignore[union-attr]
                    ).all()
                    if p.id is not None
                }
                for c in chunks:
                    if c.id is not None:
                        # ScrapedPage.url is authoritative; chunk metadata is a
                        # fallback for sessions written before it carried the URL.
                        url = page_urls.get(c.page_id) or c.parsed_meta().get("source_url", "")
                        lineage[c.id] = (c.page_id, str(url), c.chunk_index)

        records = []
        for s in samples:
            page_id, source_url, chunk_index = lineage.get(s.chunk_id, (0, "", 0))
            records.append({
                "id": s.id,
                "format": s.format,
                "system": s.system_prompt,
                "messages": s.messages(),
                "quality_score": s.quality_score,
                "session_id": s.session_id,
                # Source lineage — keep these to split the dataset correctly.
                "chunk_id": s.chunk_id if s.chunk_id is not None else 0,
                "page_id": page_id,
                "chunk_index": chunk_index,
                "source_url": source_url,
            })
        return records

    def _record_export(self, dest: str, path_or_url: str, fmt: str, count: int) -> None:
        with open_session(self.ctx.settings.db_path) as db:
            rec = ExportRecord(
                session_id=self.ctx.session_id,
                destination=dest,
                path_or_url=path_or_url,
                format=fmt,
                sample_count=count,
                stage_snapshot=self._stage,
            )
            db.add(rec)
            db.commit()
        self.ctx.export_records.append({"dest": dest, "url": path_or_url, "count": count})
