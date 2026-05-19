"""Factory app control-plane pack.

This package is declarative first-party builder/reference app surface area only:

- `config/` declares checkpoints, tools, and policies
- `prompts/` contains checkpoint prompt files
- `tools/` contains builder-specific control-plane tools
- `ui/` is reserved for control-plane UI surfaces

The control-plane runtime itself lives in `mozaiksai/control_plane/`.
"""

__all__: list[str] = []
