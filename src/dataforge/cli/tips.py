"""Contextual tips shown at each pipeline stage boundary."""
from __future__ import annotations

STAGE_TIPS: dict[str, list[str]] = {
    "discovery": [
        "No sitemap? DataForge will automatically crawl the site up to the depth you configured.",
        "Use [bold]dataforge explore <url>[/] to preview discovered URLs before running the full pipeline.",
        "Pass a direct sitemap URL (ending in .xml) to skip auto-detection entirely.",
        "Set [bold]DATAFORGE_MAX_CRAWL_PAGES[/] in your .env to control how many pages the crawler visits.",
        "Multiple seed URLs are supported — separate them with commas or provide a text file.",
    ],
    "collection": [
        "DataForge respects robots.txt by default — enable override in the pipeline wizard for sites you own.",
        "Rate limiting is per-domain. Multiple seed domains are throttled independently.",
        "Pages are saved as Markdown in the session's [bold]raw/[/] folder — inspect them anytime.",
        "Very short pages (< 50 words) are filtered out to keep your dataset clean.",
        "Scraping pauses gracefully on Ctrl+C — resume with [bold]dataforge resume <id>[/].",
    ],
    "processing": [
        "Chunks overlap slightly by default (64 tokens) to preserve context across boundaries.",
        "Adjust chunk size with [bold]DATAFORGE_CHUNK_SIZE[/] — smaller chunks work better for Q&A.",
        "Processed chunks are stored as JSONL in the session's [bold]processed/[/] folder.",
        "Token counts per chunk help estimate LLM generation cost before you commit.",
        "Use [bold]dataforge export <id>[/] after processing to get raw chunks without generating samples.",
    ],
    "generation": [
        "Generation cost scales with chunk count and [bold]n_per_chunk[/] — lower both to stay frugal.",
        "Switch to Groq or Ollama for faster and cheaper generation: [bold]dataforge config[/].",
        "The custom system prompt lets you steer tone, persona, and domain focus.",
        "Samples are saved incrementally — a partial run is never wasted.",
        "Try [bold]conversation[/] format for chat fine-tuning or [bold]instruction[/] for Alpaca-style datasets.",
    ],
    "quality": [
        "Quality scores are 1–5. Samples below 3 are rejected by default.",
        "Rejected samples are kept in the DB — you can re-evaluate them after adjusting the threshold.",
        "The quality agent uses the same LLM — a cheaper/faster model works fine here.",
        "A high rejection rate usually means your chunks were too short or off-topic.",
        "Export approved samples only, or all samples — your choice at export time.",
    ],
    "export": [
        "HuggingFace datasets are private by default — set visibility in the Hub settings after upload.",
        "Local exports write JSONL + Parquet — both are ready for [bold]datasets.load_dataset()[/].",
        "Export at any stage, not just the end — processed chunks are useful for RAG pipelines.",
        "Kaggle datasets support versioning — each export creates a new dataset version.",
        "Use [bold]--format unsloth[/] to get ShareGPT/ChatML output compatible with Unsloth training.",
    ],
}

GENERAL_TIPS: list[str] = [
    "Run [bold]dataforge info[/] to check your system resources and current configuration.",
    "Run [bold]dataforge test-llm[/] to verify your provider connection before starting a long job.",
    "Sessions are resumable at any stage — nothing is lost if you need to stop.",
    "Run [bold]dataforge update[/] to get the latest version with bug fixes and new features.",
    "Use Ollama for fully local, free generation — no API key or internet required.",
]
