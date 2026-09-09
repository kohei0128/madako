# Repository workflow

- Before changing tracked files for a new task, create a dedicated branch from `main` named `feat/<short-description>`.
- Keep unrelated user changes intact. If the worktree is not clean, preserve or separate those changes before branching.
- After implementation, run the relevant tests and build checks.
- When checks pass, commit the scoped changes, push the feature branch, and create a GitHub pull request unless the user explicitly asks not to or credentials/permissions block the operation.
- Include the verification results and any environment-related test limitations in the pull request description.
