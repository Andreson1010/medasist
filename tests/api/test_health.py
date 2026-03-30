from __future__ import annotations

from fastapi.testclient import TestClient


class TestHealth:
    def test_health_returns_200_ok(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
