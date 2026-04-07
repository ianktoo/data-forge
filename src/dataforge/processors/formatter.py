"""Structure processed chunks into dataset records."""
from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class DataRecord:
    chunk_id: int
    source_url: str
    title: str
    content: str
    token_count: int
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "source_url": self.source_url,
            "title": self.title,
            "content": self.content,
            "token_count": self.token_count,
            "metadata": self.metadata,
        }

    def to_jsonl(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


def format_records(
    chunks: list[str],
    *,
    page_id: int,
    url: str,
    title: str,
    author: str,
    date: str,
    session_id: str,
    token_counts: list[int],
) -> list[DataRecord]:
    return [
        DataRecord(
            chunk_id=page_id * 10000 + i,
            source_url=url,
            title=title,
            content=chunk,
            token_count=token_counts[i],
            metadata={
                "page_id": page_id,
                "chunk_index": i,
                "author": author,
                "date": date,
                "session_id": session_id,
            },
        )
        for i, chunk in enumerate(chunks)
    ]
