"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-30 17:21:57.126382
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "repositories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("head_sha", sa.String(length=64), nullable=True),
        sa.Column("last_ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_repositories")),
        sa.UniqueConstraint("path", name=op.f("uq_repositories_path")),
    )
    op.create_table(
        "commits",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("repository_id", sa.Integer(), nullable=False),
        sa.Column("sha", sa.String(length=64), nullable=False),
        sa.Column("parent_shas", postgresql.ARRAY(sa.String(length=64)), nullable=False),
        sa.Column("author_name", sa.Text(), nullable=False),
        sa.Column("author_email", sa.Text(), nullable=False),
        sa.Column("authored_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repositories.id"],
            name=op.f("fk_commits_repository_id_repositories"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_commits")),
        sa.UniqueConstraint("repository_id", "sha", name="uq_commits_repository_id_sha"),
    )
    op.create_index(
        "ix_commits_repository_id_committed_at",
        "commits",
        ["repository_id", "committed_at"],
        unique=False,
    )
    op.create_table(
        "file_changes",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("commit_id", sa.BigInteger(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("old_path", sa.Text(), nullable=True),
        sa.Column("change_type", sa.String(length=1), nullable=False),
        sa.Column("additions", sa.Integer(), nullable=True),
        sa.Column("deletions", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["commit_id"],
            ["commits.id"],
            name=op.f("fk_file_changes_commit_id_commits"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_file_changes")),
    )
    op.create_index(op.f("ix_file_changes_commit_id"), "file_changes", ["commit_id"], unique=False)
    op.create_index("ix_file_changes_old_path", "file_changes", ["old_path"], unique=False)
    op.create_index("ix_file_changes_path", "file_changes", ["path"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_file_changes_path", table_name="file_changes")
    op.drop_index("ix_file_changes_old_path", table_name="file_changes")
    op.drop_index(op.f("ix_file_changes_commit_id"), table_name="file_changes")
    op.drop_table("file_changes")
    op.drop_index("ix_commits_repository_id_committed_at", table_name="commits")
    op.drop_table("commits")
    op.drop_table("repositories")
