from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RepositoryCreate(BaseModel):
    path: str = Field(description="Path to a git working tree under WHY_REPOS_ROOT.")
    name: str | None = Field(default=None, max_length=255)


class RepositoryRead(ORMModel):
    id: int
    name: str
    path: str
    head_sha: str | None
    last_ingested_at: datetime | None
    created_at: datetime


class IngestRead(BaseModel):
    commits_added: int
    file_changes_added: int
    head_sha: str | None


class FileChangeRead(ORMModel):
    path: str
    old_path: str | None
    change_type: str
    additions: int | None
    deletions: int | None


class CommitSummaryRead(ORMModel):
    sha: str
    author_name: str
    author_email: str
    authored_at: datetime
    committed_at: datetime
    summary: str


class CommitRead(CommitSummaryRead):
    parent_shas: list[str]
    message: str
    file_changes: list[FileChangeRead]


class EmbedRead(BaseModel):
    commits_embedded: int
    model: str


class SearchHitRead(BaseModel):
    commit: CommitSummaryRead
    score: float = Field(description="Reciprocal Rank Fusion score; higher is better.")
    keyword_rank: int | None = Field(description="Rank in full-text results, if matched.")
    semantic_rank: int | None = Field(description="Rank in vector results, if matched.")
