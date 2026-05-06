"""AgentGenerator tool package.

Tool loading is driven by ``tools.yaml`` and file-level dynamic imports in the runtime.
This package intentionally does not auto-import every module on import, because eager
package side effects make live workflow loading non-deterministic and can mask loader
errors with unrelated import noise.
"""

__all__: list[str] = []
