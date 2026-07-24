import json

import requests

from quote_image_generator import get_quotes
import pytest


class _MockResponse:
    def __init__(self, payload: object, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code=}")

    def json(self):
        return self.payload


def test_fetch_quotable_page_parses_payload_and_returns_totals():
    response = _MockResponse({"results": [{"_id": "a"}], "totalPages": 1})

    class Session:
        def get(self, *_args, **_kwargs):
            return response

    results, total_pages = get_quotes.fetch_quotable_page(
        Session(), "https://api.example", 1, 100, 10
    )

    assert total_pages == 1
    assert results == [{"_id": "a"}]


def test_fetch_quotes_paginates_until_last_page():
    calls = []
    responses = {
        1: _MockResponse({"results": [{"_id": "a"}], "totalPages": 2}),
        2: _MockResponse({"results": [{"_id": "b"}], "totalPages": 2}),
    }

    class Session:
        def get(self, endpoint, params, timeout):
            calls.append((endpoint, params["page"], params["limit"], timeout))
            return responses[params["page"]]

    quotes = get_quotes.fetch_quotes(
        Session(), endpoint_url="https://api.example", page_limit=2, timeout_seconds=1
    )

    assert [q["_id"] for q in quotes] == ["a", "b"]
    assert calls == [
        ("https://api.example", 1, 2, 1),
        ("https://api.example", 2, 2, 1),
    ]


def test_fetch_quotable_page_rejects_bad_payload_shape():
    class Session:
        def get(self, *_args, **_kwargs):
            return _MockResponse([{"_id": "a"}])

    with pytest.raises(ValueError, match="expected a JSON object"):
        get_quotes.fetch_quotable_page(Session(), "https://api.example", 1, 150, 30)


def test_fetch_quotable_page_rejects_invalid_results_type():
    class Session:
        def get(self, *_args, **_kwargs):
            return _MockResponse({"results": {"_id": "a"}, "totalPages": 1})

    with pytest.raises(ValueError, match="'results' is not a list"):
        get_quotes.fetch_quotable_page(Session(), "https://api.example", 1, 150, 30)


def test_fetch_quotable_page_rejects_invalid_total_pages():
    class Session:
        def get(self, *_args, **_kwargs):
            return _MockResponse({"results": [], "totalPages": "2"})

    with pytest.raises(ValueError, match="'totalPages' is not an integer"):
        get_quotes.fetch_quotable_page(Session(), "https://api.example", 1, 150, 30)


def test_fetch_quotes_rejects_nonpositive_page_limit():
    with pytest.raises(ValueError, match="page_limit must be greater than zero"):
        get_quotes.fetch_quotes(session=requests.Session(), page_limit=0)


def test_fetch_quotable_page_propagates_http_errors():
    class Session:
        def get(self, *_args, **_kwargs):
            return _MockResponse({}, status_code=500)

    with pytest.raises(requests.HTTPError):
        get_quotes.fetch_quotable_page(Session(), "https://api.example", 1, 150, 30)


def test_main_writes_valid_quotes(tmp_path, monkeypatch):
    output_path = tmp_path / "quotes.json"
    monkeypatch.setattr(get_quotes, "load_project_env", lambda: None)
    monkeypatch.setenv("QUOTES_FILE_PATH", str(output_path))
    monkeypatch.setattr(
        get_quotes,
        "fetch_quotes",
        lambda *_args, **_kwargs: [{"_id": "a", "content": "c", "author": "a"}],
    )

    assert get_quotes.main() == 0

    with output_path.open("r", encoding="utf-8") as handle:
        loaded = json.load(handle)

    assert loaded == [{"_id": "a", "content": "c", "author": "a"}]


def test_main_returns_validation_error_code_on_invalid_payload(monkeypatch, tmp_path):
    output_path = tmp_path / "quotes.json"
    monkeypatch.setattr(get_quotes, "load_project_env", lambda: None)
    monkeypatch.setenv("QUOTES_FILE_PATH", str(output_path))
    monkeypatch.setattr(
        get_quotes,
        "fetch_quotes",
        lambda *_args, **_kwargs: [{"content": "missing id"}],
    )

    result = get_quotes.main()

    assert result == 1


def test_main_returns_config_error_when_quotes_file_missing(monkeypatch):
    monkeypatch.setattr(get_quotes, "load_project_env", lambda: None)
    monkeypatch.delenv("QUOTES_FILE_PATH", raising=False)

    assert get_quotes.main() == 1
