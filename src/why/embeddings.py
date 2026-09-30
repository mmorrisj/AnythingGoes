"""Turn commits into vectors for semantic search."""

import logging
from collections.abc import Sequence
from functools import cached_property, lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from why.config import get_settings
from why.models import EMBEDDING_DIMENSIONS, Commit, CommitEmbedding, FileChange

if TYPE_CHECKING:
    from fastembed import TextEmbedding

logger = logging.getLogger(__name__)

# Commits fetched and stored per round trip; larger rounds give length-sorting more to work with.
EMBED_BATCH_SIZE = 256
# Texts per ONNX inference call.
MODEL_BATCH_SIZE = 32
# Enough to capture intent; the model truncates long inputs anyway.
MAX_MESSAGE_CHARS = 2_000
MAX_PATHS = 30


class Embedder(Protocol):
    @property
    def model(self) -> str: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class FastEmbedEmbedder:
    """Local ONNX embedding model; weights are downloaded on first use."""

    def __init__(self, model: str, cache_dir: Path | None = None) -> None:
        self._model_name = model
        self._cache_dir = cache_dir

    @property
    def model(self) -> str:
        return self._model_name

    @cached_property
    def _model(self) -> "TextEmbedding":
        # Imported lazily: loading onnxruntime is slow and not needed for keyword search.
        from fastembed import TextEmbedding

        cache_dir = str(self._cache_dir) if self._cache_dir else None
        return TextEmbedding(self._model_name, cache_dir=cache_dir)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        # Each model batch is padded to its longest text, so one commit touching many files
        # would make the whole batch expensive. Embedding in length order keeps similar
        # lengths together (~4x faster on real histories); results are restored to input order.
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        vectors = self._model.passage_embed([texts[i] for i in order], batch_size=MODEL_BATCH_SIZE)
        result: list[list[float]] = [[] for _ in texts]
        for i, vector in zip(order, vectors, strict=True):
            result[i] = vector.tolist()
        return result

    def embed_query(self, text: str) -> list[float]:
        [vector] = self._model.query_embed(text)
        result: list[float] = vector.tolist()
        return result


@lru_cache
def get_embedder() -> Embedder:
    settings = get_settings()
    return FastEmbedEmbedder(settings.embedding_model, settings.embedding_cache_dir)


def commit_document(message: str, paths: Sequence[str]) -> str:
    """The text embedded for a commit: its message plus the files it touched."""
    text = message[:MAX_MESSAGE_CHARS]
    if paths:
        listed = "\n".join(paths[:MAX_PATHS])
        more = f"\n(and {len(paths) - MAX_PATHS} more)" if len(paths) > MAX_PATHS else ""
        text += f"\n\nFiles changed:\n{listed}{more}"
    return text


def embed_repository(
    session: Session, repository_id: int, embedder: Embedder, batch_size: int = EMBED_BATCH_SIZE
) -> int:
    """Embed commits that have no embedding from the current model. Returns how many.

    Commits after each batch, so an interrupted run resumes where it stopped.
    """
    total = 0
    while True:
        pending = session.execute(
            select(Commit.id, Commit.message)
            .outerjoin(CommitEmbedding, CommitEmbedding.commit_id == Commit.id)
            .where(
                Commit.repository_id == repository_id,
                or_(CommitEmbedding.commit_id.is_(None), CommitEmbedding.model != embedder.model),
            )
            .order_by(Commit.id)
            .limit(batch_size)
        ).all()
        if not pending:
            break

        paths: dict[int, list[str]] = {commit_id: [] for commit_id, _ in pending}
        for commit_id, path in session.execute(
            select(FileChange.commit_id, FileChange.path)
            .where(FileChange.commit_id.in_(paths))
            .order_by(FileChange.id)
        ):
            paths[commit_id].append(path)

        vectors = embedder.embed_documents(
            [commit_document(message, paths[commit_id]) for commit_id, message in pending]
        )
        if len(vectors) != len(pending) or any(len(v) != EMBEDDING_DIMENSIONS for v in vectors):
            raise ValueError(
                f"Embedder {embedder.model!r} must return one {EMBEDDING_DIMENSIONS}-dim "
                "vector per input"
            )

        stmt = insert(CommitEmbedding).values(
            [
                {"commit_id": commit_id, "model": embedder.model, "embedding": vector}
                for (commit_id, _), vector in zip(pending, vectors, strict=True)
            ]
        )
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=[CommitEmbedding.commit_id],
                set_={
                    "model": stmt.excluded.model,
                    "embedding": stmt.excluded.embedding,
                    "created_at": func.now(),
                },
            )
        )
        session.commit()
        total += len(pending)
        logger.info("Embedded %d commits for repository %d", total, repository_id)
    return total
