from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .llm import LLMProvider


@dataclass
class Chunk:
    id: str
    text: str
    metadata: dict[str, str]
    vector: list[float]


class InMemoryVectorStore:
    """Simple educational vector store. Swap this adapter for pgvector in production."""

    def __init__(self, llm: LLMProvider):
        self.llm = llm
        self.chunks: list[Chunk] = []

    def add(self, chunk_id: str, text: str, metadata: dict[str, str] | None = None) -> None:
        self.chunks.append(
            Chunk(
                id=chunk_id,
                text=text,
                metadata=metadata or {},
                vector=self.llm.embedding(text),
            )
        )

    def search(self, query: str, k: int = 3) -> list[tuple[Chunk, float]]:
        if not self.chunks:
            return []
        q = np.asarray(self.llm.embedding(query), dtype=float)
        scored: list[tuple[Chunk, float]] = []
        for chunk in self.chunks:
            v = np.asarray(chunk.vector, dtype=float)
            denominator = (np.linalg.norm(q) * np.linalg.norm(v)) or 1.0
            score = float(np.dot(q, v) / denominator)
            scored.append((chunk, score))
        return sorted(scored, key=lambda item: item[1], reverse=True)[:k]
