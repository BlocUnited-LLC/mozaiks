# Contributing to Mozaiks

Thanks for contributing.

## Ground Rules

- Keep changes aligned with `ARCHITECTURE.md` and `docs/architecture/`.
- Keep the AI runtime (`mozaiksai`) modular and engine-agnostic.
- Keep frontend surface semantics intact: `ask | workflow | view`.

## Development Setup

```bash
pip install -e .[dev]
```

## Required Checks

Run before opening a PR:

```bash
pytest tests/ -v
```

## Pull Request Expectations

- Explain scope and motivation.
- Call out public API changes in `mozaiksai/`.
- Update architecture docs when behavior or paths change.
- Add or update tests for behavior changes.

## Documentation Rule

If code and architecture docs diverge, update both in the same change set.

## Commit Hygiene

- Keep commits focused.
- Avoid unrelated refactors in the same PR.
- Do not include generated noise unless required.

## Security

Do not commit secrets, production tokens, or private keys.
