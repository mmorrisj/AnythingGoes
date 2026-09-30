from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Deterministic constraint names keep Alembic migrations stable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    path: Mapped[str] = mapped_column(Text, unique=True)
    # HEAD at the end of the last successful ingest; the next ingest starts here.
    head_sha: Mapped[str | None] = mapped_column(String(64))
    last_ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    commits: Mapped[list["Commit"]] = relationship(
        back_populates="repository", cascade="all, delete-orphan", passive_deletes=True
    )


class Commit(Base):
    __tablename__ = "commits"
    __table_args__ = (
        UniqueConstraint("repository_id", "sha", name="uq_commits_repository_id_sha"),
        Index("ix_commits_repository_id_committed_at", "repository_id", "committed_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"))
    # 64 chars leaves room for SHA-256 object format repositories.
    sha: Mapped[str] = mapped_column(String(64))
    parent_shas: Mapped[list[str]] = mapped_column(ARRAY(String(64)))
    author_name: Mapped[str] = mapped_column(Text)
    author_email: Mapped[str] = mapped_column(Text)
    authored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    committed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    summary: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)

    repository: Mapped[Repository] = relationship(back_populates="commits")
    file_changes: Mapped[list["FileChange"]] = relationship(
        back_populates="commit", cascade="all, delete-orphan", passive_deletes=True
    )


class FileChange(Base):
    __tablename__ = "file_changes"
    __table_args__ = (
        Index("ix_file_changes_path", "path"),
        Index("ix_file_changes_old_path", "old_path"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    commit_id: Mapped[int] = mapped_column(ForeignKey("commits.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(Text)
    # Set only for renames and copies.
    old_path: Mapped[str | None] = mapped_column(Text)
    # Git status letter: A, M, D, R, C, T.
    change_type: Mapped[str] = mapped_column(String(1))
    # NULL for binary files, where git reports no line counts.
    additions: Mapped[int | None] = mapped_column(Integer)
    deletions: Mapped[int | None] = mapped_column(Integer)

    commit: Mapped[Commit] = relationship(back_populates="file_changes")
