from __future__ import annotations

from fastapi.testclient import TestClient

from medasist.config import csv_list


class TestCsvList:
    def test_star_returns_itself(self) -> None:
        assert csv_list("*") == ["*"]

    def test_comma_separated_strips_whitespace(self) -> None:
        assert csv_list("http://a.test, http://b.test ,https://c.test") == [
            "http://a.test",
            "http://b.test",
            "https://c.test",
        ]

    def test_empty_string_defaults_to_star(self) -> None:
        assert csv_list("") == ["*"]

    def test_only_commas_defaults_to_star(self) -> None:
        assert csv_list(" , , ") == ["*"]


class TestCorsMiddleware:
    def test_simple_request_includes_cors_header(self, client: TestClient) -> None:
        response = client.get(
            "/health",
            headers={"Origin": "http://localhost:8501"},
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "*"

    def test_preflight_returns_allowed_methods(self, client: TestClient) -> None:
        response = client.options(
            "/health",
            headers={
                "Origin": "http://localhost:8501",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "*"
        assert "GET" in response.headers["access-control-allow-methods"]
