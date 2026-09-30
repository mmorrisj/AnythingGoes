from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import GitRepo


def register(client: TestClient, git_repo: GitRepo) -> int:
    response = client.post("/repositories", json={"path": str(git_repo.path)})
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_register_repository(client: TestClient, git_repo: GitRepo) -> None:
    repository_id = register(client, git_repo)

    body = client.get(f"/repositories/{repository_id}").json()
    assert body["name"] == "project"
    assert body["head_sha"] is None
    assert [r["id"] for r in client.get("/repositories").json()] == [repository_id]


def test_register_rejects_paths_outside_root(client: TestClient, tmp_path: Path) -> None:
    outside = GitRepo(tmp_path / "elsewhere")

    response = client.post("/repositories", json={"path": str(outside.path)})

    assert response.status_code == 403


def test_register_rejects_traversal_out_of_root(client: TestClient, tmp_path: Path) -> None:
    GitRepo(tmp_path / "elsewhere")
    sneaky = tmp_path / "repos" / ".." / "elsewhere"

    assert client.post("/repositories", json={"path": str(sneaky)}).status_code == 403


def test_register_rejects_non_git_directories(client: TestClient, tmp_path: Path) -> None:
    plain = tmp_path / "repos" / "plain"
    plain.mkdir(parents=True)

    assert client.post("/repositories", json={"path": str(plain)}).status_code == 422


def test_register_rejects_duplicates(client: TestClient, git_repo: GitRepo) -> None:
    register(client, git_repo)

    assert client.post("/repositories", json={"path": str(git_repo.path)}).status_code == 409


def test_ingest_and_browse_commits(client: TestClient, git_repo: GitRepo) -> None:
    git_repo.write("app.py", "print('hi')\n")
    first = git_repo.commit("Add app")
    git_repo.write("README.md", "docs\n")
    git_repo.commit("Add docs")
    git_repo.git("mv", "app.py", "main.py")
    renamed = git_repo.commit("Rename app to main")
    repository_id = register(client, git_repo)

    ingest = client.post(f"/repositories/{repository_id}/ingest").json()
    assert ingest["commits_added"] == 3
    assert ingest["head_sha"] == renamed

    commits = client.get(f"/repositories/{repository_id}/commits").json()
    assert [c["summary"] for c in commits] == ["Rename app to main", "Add docs", "Add app"]

    # Path filter follows the rename via old_path.
    history = client.get(f"/repositories/{repository_id}/commits", params={"path": "app.py"})
    assert [c["sha"] for c in history.json()] == [renamed, first]

    page = client.get(f"/repositories/{repository_id}/commits", params={"limit": 1, "offset": 1})
    assert [c["summary"] for c in page.json()] == ["Add docs"]

    detail = client.get(f"/repositories/{repository_id}/commits/{renamed}").json()
    assert detail["parent_shas"] and detail["message"] == "Rename app to main"
    assert detail["file_changes"] == [
        {"path": "main.py", "old_path": "app.py", "change_type": "R", "additions": 0,
         "deletions": 0},
    ]  # fmt: skip


def test_not_found(client: TestClient) -> None:
    assert client.get("/repositories/999").status_code == 404
    assert client.post("/repositories/999/ingest").status_code == 404
    assert client.get("/repositories/999/commits").status_code == 404
    assert client.get(f"/repositories/999/commits/{'a' * 40}").status_code == 404
