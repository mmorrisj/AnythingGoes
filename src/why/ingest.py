"""Load a repository's git history into the database."""

import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import islice
from pathlib import Path
from typing import Any, TypeVar

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from why.git_log import ParsedCommit, iter_commits, resolve_rev
from why.models import Commit, FileChange, Repository

logger = logging.getLogger(__name__)

COMMIT_BATCH_SIZE = 500
# Keeps each INSERT well under Postgres' 65,535 bind-parameter limit.
FILE_CHANGE_BATCH_SIZE = 5_000

T = TypeVar("T")


@dataclass(frozen=True)
class IngestResult:
    commits_added: int
    file_changes_added: int
    head_sha: str | None


def ingest_repository(session: Session, repository: Repository) -> IngestResult:
    """Import commits added since the last ingest, up to the current HEAD.

    Safe to re-run: commits already stored are skipped. If the previously ingested
    head no longer exists (e.g. after a force push), the full history is re-scanned.
    """
    repo_path = Path(repository.path)
    head = resolve_rev(repo_path, "HEAD")
    if head is None:
        logger.info("Repository %s has no commits yet", repository.path)
        return IngestResult(0, 0, None)

    since = repository.head_sha
    if since is not None and resolve_rev(repo_path, since) is None:
        logger.warning("Previous head %s is gone from %s; rescanning", since, repository.path)
        since = None

    commits_added = file_changes_added = 0
    for batch in _batched(iter_commits(repo_path, "HEAD", since=since), COMMIT_BATCH_SIZE):
        added, changes = _store_batch(session, repository.id, batch)
        commits_added += added
        file_changes_added += changes

    repository.head_sha = head
    repository.last_ingested_at = datetime.now(UTC)
    session.commit()
    logger.info(
        "Ingested %d commits (%d file changes) from %s",
        commits_added, file_changes_added, repository.path,
    )  # fmt: skip
    return IngestResult(commits_added, file_changes_added, head)


def _store_batch(
    session: Session, repository_id: int, batch: list[ParsedCommit]
) -> tuple[int, int]:
    commit_rows = [
        {
            "repository_id": repository_id,
            "sha": c.sha,
            "parent_shas": c.parent_shas,
            "author_name": c.author_name,
            "author_email": c.author_email,
            "authored_at": c.authored_at,
            "committed_at": c.committed_at,
            "summary": c.summary,
            "message": c.message,
        }
        for c in batch
    ]
    stmt = (
        insert(Commit)
        .values(commit_rows)
        .on_conflict_do_nothing(constraint="uq_commits_repository_id_sha")
        .returning(Commit.id, Commit.sha)
    )
    # Only commits that were actually inserted get file changes, keeping re-runs idempotent.
    inserted = {sha: commit_id for commit_id, sha in session.execute(stmt)}

    change_rows: list[dict[str, Any]] = [
        {
            "commit_id": inserted[c.sha],
            "path": fc.path,
            "old_path": fc.old_path,
            "change_type": fc.change_type,
            "additions": fc.additions,
            "deletions": fc.deletions,
        }
        for c in batch
        if c.sha in inserted
        for fc in c.file_changes
    ]
    for chunk in _batched(change_rows, FILE_CHANGE_BATCH_SIZE):
        session.execute(insert(FileChange), chunk)

    session.flush()
    return len(inserted), len(change_rows)


def get_or_create_repository(session: Session, path: Path, name: str | None = None) -> Repository:
    resolved = str(path.resolve())
    repository = session.scalar(select(Repository).where(Repository.path == resolved))
    if repository is None:
        repository = Repository(path=resolved, name=name or path.resolve().name)
        session.add(repository)
        session.commit()
    return repository


def _batched(items: Iterable[T], size: int) -> Iterator[list[T]]:
    iterator = iter(items)
    while batch := list(islice(iterator, size)):
        yield batch
