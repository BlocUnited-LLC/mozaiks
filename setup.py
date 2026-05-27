from pathlib import Path

from setuptools import find_namespace_packages, find_packages, setup

PACKAGE_PREFIXES = (
    "mozaiksai",
    "mozaiks",
    "mozaiks_cli",
    "logs",
    "factory_app",
    "web_shell",
)


def _matches_prefix(package_name: str) -> bool:
    return any(
        package_name == prefix or package_name.startswith(f"{prefix}.")
        for prefix in PACKAGE_PREFIXES
    )


def _discover_repo_packages() -> set[str]:
    discovered = set()
    for package_name in find_packages(where="."):
        if _matches_prefix(package_name):
            discovered.add(package_name)
    for package_name in find_namespace_packages(where="."):
        if _matches_prefix(package_name):
            discovered.add(package_name)
    return discovered


def _discover_chat_ui_packages() -> set[str]:
    root = Path("chat-ui")
    packages = {"mozaiks_chat_ui"}
    for directory in root.rglob("*"):
        if not directory.is_dir():
            continue
        relative = directory.relative_to(root)
        if not relative.parts:
            continue
        if not all(part.isidentifier() for part in relative.parts):
            continue
        packages.add("mozaiks_chat_ui." + ".".join(relative.parts))
    return packages


packages = sorted(_discover_repo_packages() | _discover_chat_ui_packages())


setup(
    packages=packages,
    package_dir={"mozaiks_chat_ui": "chat-ui"},
)
