#!/usr/bin/env python3
"""Render a deterministic GitHub-backed contributors row in README.md.

GitHub's contributors endpoint provides linked commit authors, while local
git history catches newly merged GitHub noreply authors before API caches
refresh. This never awards credit solely for opening a pull request.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.error
import urllib.request

REPO = "BlocUnited-LLC/mozaiks"
START = "<!-- contributors:auto:start -->"
END = "<!-- contributors:auto:end -->"
NOREPLY = re.compile(r"^(?:[0-9]+\\+)?([A-Za-z0-9-]+)@users\\.noreply\\.github\\.com$", re.I)
VALID_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
EXCLUDED = {"dependabot", "dependabot[bot]", "github-actions[bot]", "claude", "codex", "web-flow"}
MAX_CONTRIBUTORS = 100


def eligible(login: str) -> bool:
    return bool(VALID_LOGIN.fullmatch(login)) and login.lower() not in EXCLUDED and not login.lower().endswith("[bot]")


def history_logins() -> set[str]:
    # Only commits reachable from checked-out main are eligible.
    output = subprocess.check_output(
        ["git", "log", "--format=%ae", "HEAD"], text=True
    )
    found: set[str] = set()
    for email in output.splitlines():
        match = NOREPLY.fullmatch(email.strip())
        if match and eligible(match.group(1)):
            found.add(match.group(1))
    return found


def api_contributors(repo: str, token: str) -> set[str]:
    found: set[str] = set()
    for page in range(1, 21):
        url = f"https://api.github.com/repos/{repo}/contributors?per_page=100&page={page}"
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "mozaiks-contributor-sync"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.load(response)
        if not isinstance(data, list):
            raise ValueError("Unexpected GitHub contributors response")
        for entry in data:
            login = entry.get("login", "")
            if isinstance(login, str) and eligible(login) and entry.get("type") == "User":
                found.add(login)
        if len(data) < 100:
            break
    return found


def render(logins: set[str]) -> str:
    ordered = sorted((name for name in logins if eligible(name)), key=str.casefold)
    if len(ordered) > MAX_CONTRIBUTORS:
        raise ValueError(f"More than {MAX_CONTRIBUTORS} contributors: increase the limit explicitly")
    lines = [
        START,
        "<p align=\"center\">",
    ]
    for login in ordered:
        lines.append(
            f'  <a href="https://github.com/{login}" title="{login}">'
            f'<img src="https://github.com/{login}.png?size=80" '
            f'width="64" height="64" alt="{login}" /></a>'
        )
    lines += ["</p>", END]
    return "\n".join(lines)


def update_readme(text: str, generated: str) -> str:
    if text.count(START) != 1 or text.count(END) != 1:
        raise ValueError("README must contain exactly one contributors marker pair")
    before, rest = text.split(START, 1)
    _, after = rest.split(END, 1)
    return before + generated + after


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--readme", type=Path, default=Path("README.md"))
    args = parser.parse_args()
    original = args.readme.read_text(encoding="utf-8")
    logins = api_contributors(REPO, os.environ.get("GITHUB_TOKEN", ""))
    logins.update(history_logins())
    updated = update_readme(original, render(logins))
    if args.check:
        if updated != original:
            raise SystemExit("Contributors section is stale")
    elif updated != original:
        args.readme.write_text(updated, encoding="utf-8")
    print(f"Contributors synchronized: {len(logins)}")


if __name__ == "__main__":
    main()
