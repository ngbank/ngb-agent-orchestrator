from orchestrator.code_generator.edges import (
    route_after_clone,
    route_after_fetch_token,
    route_after_prepare_workspace,
    route_after_repo_setup,
    route_after_resolve,
)


def test_route_after_resolve_success():
    assert route_after_resolve({}) == "fetch_github_token"


def test_route_after_fetch_token_failure():
    assert route_after_fetch_token({"exec_error": "boom"}) == "persist_results"


def test_route_after_clone_success():
    assert route_after_clone({}) == "run_goose"


def test_route_after_prepare_workspace_re_execution():
    assert route_after_prepare_workspace({"pr_approval_decision": "commented"}) == "run_goose"


def test_route_after_prepare_workspace_first_execution():
    assert route_after_prepare_workspace({}) == "infer_branch_prefix"


def test_route_after_repo_setup_failure():
    assert route_after_repo_setup({"exec_error": "boom"}) == "persist_results"


def test_route_after_repo_setup_success():
    assert route_after_repo_setup({"working_dir": "/tmp/work"}) == "run_goose"
