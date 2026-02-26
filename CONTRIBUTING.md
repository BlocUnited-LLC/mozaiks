# Contributing to Mozaiks

Thanks for contributing.

## Ground Rules

- Keep changes aligned with `docs/architecture/source-of-truth/`.
- Preserve layer direction: `contracts <- core <- orchestration`.
- Do not add imports from `mozaiks.orchestration` into `mozaiks.core`.
- Keep frontend surface semantics intact: `ask | workflow | view` and `view != sandbox`.

## Development Setup

```bash
pip install -e .[dev]
```

## Required Checks

Run before opening a PR:

```bash
pytest tests/ -v
mypy src/mozaiks/
ruff check src/
```

## Pull Request Expectations

- Explain scope and motivation.
- Call out public API changes (`mozaiks.core.*`, `mozaiks.orchestration.*`, `mozaiks.contracts.*`).
- Update source-of-truth docs when behavior or paths change.
- Add or update tests for behavior changes.

## Documentation Rule

If code and source-of-truth docs diverge, update both in the same change set.

## Commit Hygiene

- Keep commits focused.
- Avoid unrelated refactors in the same PR.
- Do not include generated noise unless required.

## Security

Do not commit secrets, production tokens, or private keys.
