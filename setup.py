"""Setuptools package discovery for the flat-layout Mozaiks repository."""

from __future__ import annotations

from setuptools import find_namespace_packages, setup

PACKAGE_INCLUDES = [
    "factory_app",
    "factory_app.*",
    "logs",
    "logs.*",
    "mozaiks",
    "mozaiks.*",
    "mozaiks_cli",
    "mozaiks_cli.*",
    "mozaiksai",
    "mozaiksai.*",
    "web_shell",
    "web_shell.*",
]

PACKAGE_EXCLUDES = [
    "tests",
    "tests.*",
    "web_shell.node_modules",
    "web_shell.node_modules.*",
    "web_shell.dist",
    "web_shell.dist.*",
    "web_shell.playwright",
    "web_shell.playwright.*",
]


packages = find_namespace_packages(include=PACKAGE_INCLUDES, exclude=PACKAGE_EXCLUDES)
packages.append("mozaiks_chat_ui")
packages.extend(
    f"mozaiks_chat_ui.{package_name}"
    for package_name in find_namespace_packages(
        where="chat-ui",
        exclude=[
            "dist",
            "dist.*",
            "node_modules",
            "node_modules.*",
        ],
    )
)
packages = sorted(set(packages))

setup(
    packages=packages,
    package_dir={"mozaiks_chat_ui": "chat-ui"},
)
