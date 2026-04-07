"""Shared fixtures."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from dataforge.config.settings import Settings
from dataforge.storage.database import init_db


@pytest.fixture
def tmp_settings(tmp_path: Path) -> Settings:
    s = Settings(
        output_dir=tmp_path / "output",
        db_path=tmp_path / "test.db",
        llm_provider="openai",
        llm_model="gpt-4o-mini",
        rate_limit=100.0,
        chunk_size=256,
        chunk_overlap=32,
    )
    init_db(s.db_path)
    return s


SAMPLE_HTML = """
<html>
<head><title>Test Article</title>
<meta name="author" content="Jane Doe">
</head>
<body>
<nav>Navigation menu</nav>
<main>
<h1>How to Train Your LLM</h1>
<p>Fine-tuning large language models requires high-quality data.
The process involves collecting, cleaning, and structuring text into
instruction-response pairs that guide the model's behaviour.</p>
<p>Synthetic data generation is an increasingly popular technique.
By prompting a capable model to produce training examples grounded in
real content, practitioners can scale dataset creation without relying
solely on human annotation.</p>
<p>Key considerations include diversity of topics, balance between
question types, and rigorous quality filtering to remove low-signal samples.</p>
</main>
<footer>Copyright 2024</footer>
</body>
</html>
"""
