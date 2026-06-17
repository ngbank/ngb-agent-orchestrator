from unittest.mock import patch


def test_fetch_github_token_success_writes_token_to_state():
    from graph.code_generator.nodes.fetch_github_token import fetch_github_token

    with patch(
        "graph.code_generator.nodes.fetch_github_token.get_installation_token",
        return_value="token-123",
    ):
        result = fetch_github_token({"ticket_key": "AOS-120"})

    assert result == {"github_token": "token-123"}


def test_fetch_github_token_failure_sets_exec_error_and_routes_to_persist_results():
    from dispatcher.github_client import GitHubAuthError
    from graph.code_generator.nodes.fetch_github_token import fetch_github_token

    with patch(
        "graph.code_generator.nodes.fetch_github_token.get_installation_token",
        side_effect=GitHubAuthError("bad credentials"),
    ):
        result = fetch_github_token({"ticket_key": "AOS-120"})

    assert result["failed_node"] == "execute_plan"
    assert result["exec_error"] == "GitHub token fetch failed: bad credentials"
    assert result["execution_summary"]["status"] == "failed"
