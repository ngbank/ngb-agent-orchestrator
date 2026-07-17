## Commit messages

Use Conventional Commits: `type(scope): description`

- `type` is one of `feat`, `fix`, `chore`, `refactor`, `docs`, `style`, `test`.
- `scope` is optional. When present it's either a ticket ID (e.g. `AOS-123`) or a short topic word (e.g. `devcontainer`).
- Examples: `fix(AOS-280): retry respects human-decision gates`, `feat: add Codespaces devcontainer configuration`.

Enforced by the `conventional-pre-commit` commit-msg hook in `.pre-commit-config.yaml`.
