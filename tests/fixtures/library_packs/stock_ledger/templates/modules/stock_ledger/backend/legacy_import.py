"""One-off CSV import kept for version 1 only; version 2 removes it."""


def parse_rows(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]
