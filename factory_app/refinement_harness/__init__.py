"""Factory app refinement harness.

This package is declarative first-party builder/reference app surface area only:

- `config/` declares checkpoints, tools, and policies
- `prompts/` contains checkpoint prompt files
- `tools/` contains builder-specific refinement tools
- `ui/` is reserved for future refinement harness UI surfaces

The refinement engine runtime currently lives in `mozaiksai/control_plane/`
for import compatibility.
"""

__all__: list[str] = []
