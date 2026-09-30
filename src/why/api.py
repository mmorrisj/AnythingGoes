from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session, selectinload

from why.config import Settings, get_settings
from why.db import get_session
from why.git_log import GitError, is_git_repo
from why.ingest import ingest_repository
from why.models import Commit, FileChange, Repository
from why.schemas import (
    CommitRead,
    CommitSummaryRead,
    IngestRead,
    RepositoryCreate,
    RepositoryRead,
)

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("/health")
def health(session: SessionDep) -> dict[str, str]:
    session.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.post("/repositories", status_code=status.HTTP_201_CREATED)
def create_repository(
    body: RepositoryCreate, session: SessionDep, settings: SettingsDep
) -> RepositoryRead:
    path = Path(body.path).resolve()
    if not path.is_relative_to(settings.repos_root.resolve()):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Path is outside WHY_REPOS_ROOT")
    if not is_git_repo(path):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Path is not a git repository")
    if session.scalar(select(Repository.id).where(Repository.path == str(path))) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Repository already registered")

    repository = Repository(path=str(path), name=body.name or path.name)
    session.add(repository)
    session.commit()
    return RepositoryRead.model_validate(repository)


@router.get("/repositories")
def list_repositories(session: SessionDep) -> list[RepositoryRead]:
    repositories = session.scalars(select(Repository).order_by(Repository.id))
    return [RepositoryRead.model_validate(r) for r in repositories]


@router.get("/repositories/{repository_id}")
def get_repository(repository_id: int, session: SessionDep) -> RepositoryRead:
    return RepositoryRead.model_validate(_get_repository_or_404(session, repository_id))


@router.post("/repositories/{repository_id}/ingest")
def ingest(repository_id: int, session: SessionDep) -> IngestRead:
    # Row lock serialises concurrent ingests of the same repository.
    repository = _get_repository_or_404(session, repository_id, for_update=True)
    try:
        result = ingest_repository(session, repository)
    except GitError as exc:
        session.rollback()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return IngestRead(
        commits_added=result.commits_added,
        file_changes_added=result.file_changes_added,
        head_sha=result.head_sha,
    )


@router.get("/repositories/{repository_id}/commits")
def list_commits(
    repository_id: int,
    session: SessionDep,
    path: Annotated[str | None, Query(description="Only commits touching this file.")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[CommitSummaryRead]:
    _get_repository_or_404(session, repository_id)
    stmt = select(Commit).where(Commit.repository_id == repository_id)
    if path is not None:
        touching = select(FileChange.commit_id).where(
            or_(FileChange.path == path, FileChange.old_path == path)
        )
        stmt = stmt.where(Commit.id.in_(touching))
    stmt = stmt.order_by(Commit.committed_at.desc(), Commit.id.desc()).limit(limit).offset(offset)
    return [CommitSummaryRead.model_validate(c) for c in session.scalars(stmt)]


@router.get("/repositories/{repository_id}/commits/{sha}")
def get_commit(repository_id: int, sha: str, session: SessionDep) -> CommitRead:
    commit = session.scalar(
        select(Commit)
        .where(Commit.repository_id == repository_id, Commit.sha == sha)
        .options(selectinload(Commit.file_changes))
    )
    if commit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Commit not found")
    return CommitRead.model_validate(commit)


def _get_repository_or_404(
    session: Session, repository_id: int, *, for_update: bool = False
) -> Repository:
    repository = session.get(Repository, repository_id, with_for_update=for_update)
    if repository is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Repository not found")
    return repository
