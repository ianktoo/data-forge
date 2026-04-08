"""ExporterAgent — package dataset and upload to configured targets."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from sqlmodel import select

from dataforge.cli.preflight import check_export_target
from dataforge.exporters.local import export_all_formats
from dataforge.storage import ExportRecord, SyntheticSample, open_session
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
    ) -> None:
        super().__init__(context)
        self._stage = stage_snapshot
        self._approved_only = approved_only
        self._targets = targets or ["local"]
        self._hf_repo  = hf_repo_id
        self._hf_priv  = hf_private
        self._kg_slug  = kaggle_slug
        self._kg_title = kaggle_title

    async def run(self) -> PipelineContext:
        s = self.ctx.settings
        records = self._load_samples()
        if not records:
            self.log.warning("No samples to export")
            return self.ctx

        export_dir = self.ctx.session_dir() / "exports" / datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        self.log.info(f"Exporting {len(records)} samples → {export_dir}")

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

    def _load_samples(self) -> list[dict]:
        with open_session(self.ctx.settings.db_path) as db:
            q = select(SyntheticSample).where(
                SyntheticSample.session_id == self.ctx.session_id
            )
            if self._approved_only:
                q = q.where(SyntheticSample.approved == True)  # noqa: E712
            samples = db.exec(q).all()

        return [
            {
                "id": s.id,
                "format": s.format,
                "system": s.system_prompt,
                "messages": s.messages(),
                "quality_score": s.quality_score,
                "session_id": s.session_id,
            }
            for s in samples
        ]

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
