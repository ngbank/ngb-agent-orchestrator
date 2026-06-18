from orchestrator.code_generator.edges import (
    route_after_clone,
    route_after_fetch_token,
    route_after_resolve,
)


def test_route_after_resolve_success():
    assert route_after_resolve({}) == "fetch_github_token"


def test_route_after_fetch_token_failure():
    assert route_after_fetch_token({"exec_error": "boom"}) == "persist_results"


def test_route_after_clone_success():
    assert route_after_clone({}) == "run_goose"
