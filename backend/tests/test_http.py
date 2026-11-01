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


@pytest.mark.parametrize("body", [
    {"name": "price_spike", "offer_id": "abc"},
    {"name": "out_of_stock"},
    {"name": "price_spike", "offer_id": "nope:1"},
    {"name": "price_spike", "offer_id": "techmart:NOPE"},
    {"name": "payee_swap"},
    {"name": "teleport", "offer_id": "techmart:TM-1001"},
])
def test_bad_sandbox_scenario_is_client_error(body):
    from fastapi.testclient import TestClient
    r = TestClient(main.app, raise_server_exceptions=False).post("/api/sandbox/scenario", json=body)
    assert 400 <= r.status_code < 500


def test_security_headers(client, dist):
    for path in ["/", "/api/health"]:
        h = client.get(path).headers
        assert "frame-ancestors 'none'" in h["content-security-policy"]  # окно подтверждения нельзя встроить в чужой сайт
        assert "default-src 'self'" in h["content-security-policy"]
        assert h["x-frame-options"] == "DENY"
        assert h["x-content-type-options"] == "nosniff"
        assert h["referrer-policy"] == "no-referrer"
    assert client.get("/api/health").headers["cache-control"] == "no-store"


def test_api_docs_do_not_load_cdn_by_default(client):
    # Swagger UI тянет скрипты с CDN — в закрытом контуре это внешний ресурс
    assert client.get("/docs").status_code == 404 or "cdn" not in client.get("/docs").text


def test_llm_error_does_not_leak_internal_address(client, monkeypatch):
    import httpx

    from app.agent import llm

    def boom(text, today):
        req = httpx.Request("POST", "http://vllm.internal:8000/v1/chat/completions")
        raise httpx.HTTPStatusError("Server error '503' for url 'http://vllm.internal:8000/v1/chat/completions'",
                                    request=req, response=httpx.Response(503, request=req))
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "PROVIDER", "openai")
    monkeypatch.setattr(llm, "_call_openai", boom)
    t = client.post("/api/tasks", json={"text": "SSD Samsung 2 ТБ"}).json()
    assert t["parser"] == "rules"
    assert "vllm.internal" not in str(t)


def test_static_is_gzipped_but_api_is_not(client, dist):
    (dist / "assets" / "big.js").write_text("console.log(1);" * 500, encoding="utf-8")
    r = client.get("/assets/big.js", headers={"Accept-Encoding": "gzip"})
    assert r.headers.get("content-encoding") == "gzip"
    t = client.post("/api/tasks", json={"text": "Найди SSD Samsung 2 ТБ с гарантией и доставкой до пятницы"},
                    headers={"Accept-Encoding": "gzip"})
    assert len(t.content) > 1024 and "content-encoding" not in t.headers  # ответы API не сжимаются (BREACH)


@pytest.mark.parametrize("chunked", [False, True])
def test_oversized_api_body_is_rejected(client, chunked):
    # Было: тело 30 МБ читалось и разбиралось целиком (без nginx, при make run). Самое длинное законное тело —
    # поручение до 500 символов, поэтому всё больше 64 КБ — 413, в том числе без Content-Length (передача по частям).
    big = ('{"text": "' + "x" * 200_000 + '"}').encode()
    if chunked:
        r = client.post("/api/tasks", content=iter([big[i:i + 8192] for i in range(0, len(big), 8192)]),
                        headers={"content-type": "application/json"})
    else:
        r = client.post("/api/tasks", content=big, headers={"content-type": "application/json"})
    assert r.status_code == 413
    assert client.post("/api/tasks", json={"text": "SSD Samsung 2 ТБ"}).status_code == 200
