import hashlib
import math
import os
import re
import subprocess
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from why.config import Settings, get_settings
from why.db import get_session
from why.embeddings import get_embedder
from why.main import app
from why.models import EMBEDDING_DIMENSIONS

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TEST_DB = "postgresql+psycopg://why:why@localhost:5432/why_test"


class GitRepo:
    """A throwaway git repository with deterministic commit timestamps."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._tick = 0
        path.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Ada Lovelace")
        self.git("config", "user.email", "ada@example.com")
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args: str) -> str:
        date = f"2024-01-01T00:{self._tick // 60:02d}:{self._tick % 60:02d}+00:00"
        env = {**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
        result = subprocess.run(
            ["git", *args], cwd=self.path, env=env, check=True, capture_output=True, text=True
        )
        return result.stdout.strip()

    def write(self, name: str, content: str | bytes) -> None:
        target = self.path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content)

    def commit(self, message: str, *extra: str) -> str:
        self._tick += 1
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message, *extra)
        return self.git("rev-parse", "HEAD")


class HashEmbedder:
    """Deterministic bag-of-words embedder: texts sharing words get similar vectors.

    Stands in for the real model so tests need no download and are reproducible.
    """

    def __init__(self, model: str = "test-hash") -> None:
        self.model = model

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * EMBEDDING_DIMENSIONS
        for word in re.findall(r"[a-z]+", text.lower()):
            digest = hashlib.sha256(word.encode()).digest()
            vector[int.from_bytes(digest[:4], "big") % EMBEDDING_DIMENSIONS] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]


@pytest.fixture
def embedder() -> HashEmbedder:
    return HashEmbedder()


@pytest.fixture
def git_repo(tmp_path: Path) -> GitRepo:
    return GitRepo(tmp_path / "repos" / "project")


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    url = os.environ.get("WHY_TEST_DATABASE_URL", DEFAULT_TEST_DB)
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    except OperationalError:
        pytest.skip(f"test database unavailable at {url}")

    # Build the schema through the real migrations so they are tested too.
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, "head")

    yield engine
    engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE repositories RESTART IDENTITY CASCADE"))


@pytest.fixture
def client(session: Session, tmp_path: Path, embedder: HashEmbedder) -> Iterator[TestClient]:
    settings = Settings(database_url="unused", repos_root=tmp_path / "repos")
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_embedder] = lambda: embedder
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
