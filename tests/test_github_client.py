from unittest.mock import MagicMock, patch

import pytest
import requests

from dispatcher.github_client import (
    GitHubAuthError,
    _parse_repo_url,
    add_pr_comment,
    create_pr,
    get_installation_token,
    get_open_pr,
)


def test_get_installation_token_signs_jwt_and_calls_github(monkeypatch):
    monkeypatch.setenv("GITHUB_APP_ID", "123")
    monkeypatch.setenv("GITHUB_APP_INSTALLATION_ID", "456")
    monkeypatch.setenv(
        "GITHUB_APP_PRIVATE_KEY",
        "pem-inline-value\\nabc\\npem-inline-value",
    )

    with (
        patch("dispatcher.github_client.jwt.encode", return_value="jwt-token") as mock_encode,
        patch("dispatcher.github_client.requests.post") as mock_post,
    ):
        response = MagicMock()
        response.json.return_value = {"token": "inst-token"}
        response.raise_for_status.return_value = None
        mock_post.return_value = response

        token = get_installation_token("AOS")

    assert token == "inst-token"
    payload = mock_encode.call_args.args[0]
    assert payload["iss"] == "123"
    assert payload["exp"] > payload["iat"]
    assert "/app/installations/456/access_tokens" in mock_post.call_args.args[0]


def test_get_installation_token_raises_on_http_error(monkeypatch):
    monkeypatch.setenv("GITHUB_APP_ID", "123")
    monkeypatch.setenv("GITHUB_APP_INSTALLATION_ID", "456")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "key")

    with (
        patch("dispatcher.github_client.jwt.encode", return_value="jwt-token"),
        patch("dispatcher.github_client.requests.post") as mock_post,
    ):
        mock_post.side_effect = requests.RequestException("boom")
        with pytest.raises(GitHubAuthError):
            get_installation_token("AOS")


def test_get_open_pr_returns_url_when_found():
    with patch("dispatcher.github_client.requests.get") as mock_get:
        response = MagicMock()
        response.json.return_value = [{"html_url": "https://github.com/org/repo/pull/1"}]
        response.raise_for_status.return_value = None
        mock_get.return_value = response

        assert (
            get_open_pr("org", "repo", "feature/test", "tok")
            == "https://github.com/org/repo/pull/1"
        )


def test_get_open_pr_returns_none_when_empty():
    with patch("dispatcher.github_client.requests.get") as mock_get:
        response = MagicMock()
        response.json.return_value = []
        response.raise_for_status.return_value = None
        mock_get.return_value = response

        assert get_open_pr("org", "repo", "feature/test", "tok") is None


def test_create_pr_posts_correct_payload():
    with patch("dispatcher.github_client.requests.post") as mock_post:
        response = MagicMock()
        response.json.return_value = {"html_url": "https://github.com/org/repo/pull/2"}
        response.raise_for_status.return_value = None
        mock_post.return_value = response

        pr_url = create_pr("org", "repo", "feature/test", "main", "title", "body", "tok")

    assert pr_url.endswith("/pull/2")
    assert mock_post.call_args.kwargs["json"]["head"] == "feature/test"


def test_add_pr_comment_parses_url_and_posts():
    with patch("dispatcher.github_client.requests.post") as mock_post:
        response = MagicMock()
        response.raise_for_status.return_value = None
        mock_post.return_value = response

        add_pr_comment("https://github.com/org/repo/pull/12", "hello", "tok")

    assert "/repos/org/repo/issues/12/comments" in mock_post.call_args.args[0]
    assert mock_post.call_args.kwargs["json"] == {"body": "hello"}


def test_parse_repo_url_https_and_ssh():
    assert _parse_repo_url("https://github.com/org/repo.git") == ("org", "repo")
    assert _parse_repo_url("git@github.com:org/repo.git") == ("org", "repo")
