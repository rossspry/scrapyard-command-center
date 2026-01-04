# Development guide

This repository is still being bootstrapped. The goals of this guide are to make early contributions consistent and to reduce churn as the codebase grows.

## Working practices
- Keep changes small and focused. If a feature requires multiple layers (API, UI, data), split it into incremental pull requests when possible.
- Prefer documentation alongside code. Update or create docs when behavior changes.
- Use descriptive branch names (for example, `feature/<topic>` or `docs/<topic>`).

## Pull request expectations
- Explain the problem the change solves and how you validated it.
- Include screenshots or terminal output when helpful for reviewers.
- Add or update tests once the project gains automated coverage.

## Directory layout
- `docs/` — high-level documentation. Add new pages here when you introduce a new subsystem or workflow.
- `src/` — application code (to be added as the project takes shape).
- `tests/` — automated tests (to be added alongside implementation).

## Getting started locally
Implementation work has not begun. As the project adds services or tooling, document prerequisites and setup steps in this section.
