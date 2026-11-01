"""HTTP-слой: раздача фронтенда и служебные ответы."""
import pytest

from app import main


@pytest.fixture
def dist(tmp_path, monkeypatch):
    d = tmp_path / "frontend" / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<!doctype html><div id=root></div>", encoding="utf-8")
    (d / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("SECRET", encoding="utf-8")
    monkeypatch.setattr(main, "DIST", d)
    return d


@pytest.mark.parametrize("path", ["/..%2Fsecret.txt", "/..%2F..%2Fsecret.txt", "/%2E%2E/secret.txt", "/assets/..%2F..%2Fsecret.txt"])
def test_spa_does_not_serve_files_outside_dist(client, dist, path):
    r = client.get(path)
    assert "SECRET" not in r.text


def test_spa_serves_index_and_assets(client, dist):
    assert "id=root" in client.get("/").text
    assert "id=root" in client.get("/orders/123").text  # маршруты SPA
    assert client.get("/assets/app.js").text == "console.log(1)"


def test_unknown_api_route_is_404_not_spa(client, dist):
    r = client.get("/api/nope")
    assert r.status_code == 404 and "id=root" not in r.text
