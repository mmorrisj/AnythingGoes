"""commit search

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-30 19:37:49.624168
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Requires a role allowed to create extensions (e.g. the database owner on pgvector images).
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.add_column(
        "commits",
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english', message)", persisted=True),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_commits_search_vector", "commits", ["search_vector"], postgresql_using="gin"
    )

    op.create_table(
        "commit_embeddings",
        sa.Column("commit_id", sa.BigInteger(), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("embedding", Vector(384), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["commit_id"],
            ["commits.id"],
            name=op.f("fk_commit_embeddings_commit_id_commits"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("commit_id", name=op.f("pk_commit_embeddings")),
    )
    op.create_index(
        "ix_commit_embeddings_embedding",
        "commit_embeddings",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_table("commit_embeddings")
    op.drop_index("ix_commits_search_vector", table_name="commits")
    op.drop_column("commits", "search_vector")
    # The vector extension is left installed; other schemas may depend on it.
