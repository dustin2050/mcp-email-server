from starlette.testclient import TestClient

from mcp_email_server.app import mcp


def test_healthz_returns_ok():
    with TestClient(mcp.streamable_http_app()) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
