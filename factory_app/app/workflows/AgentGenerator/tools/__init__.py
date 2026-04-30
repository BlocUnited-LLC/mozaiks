# ==============================================================================
# FILE: Generator/tools/__init__.py
# DESCRIPTION: Dynamic tools module initialization - Auto-discovers and imports tools
# LOGIC: This module dynamically discovers and imports tool functions from Python files in the current directory.
# It uses the following logic:
# 1. Searches for all Python files in the directory, excluding __init__.py and files starting with an underscore.
# 2. Dynamically imports each module.
# 3. Inspects the module for async functions that:
#    - Do not start with an underscore.
#    - Are defined within the module itself (not imported).
# 4. Adds these functions to the global namespace and builds a dynamic __all__ list.
# ==============================================================================

import importlib
import inspect
from pathlib import Path
from typing import Any, Dict

# Get the current directory
current_dir = Path(__file__).parent

def _discover_and_import_tools() -> Dict[str, Any]:
    """Discover and import all tool functions from modules in this directory."""
    discovered_tools = {}
    
    # Auto-discover and import all Python files in this directory
    for file_path in current_dir.glob("*.py"):
        # Skip __init__.py and any files starting with underscore
        if file_path.name == "__init__.py" or file_path.name.startswith("_"):
            continue
        
        module_name = file_path.stem  # filename without extension
        
        try:
            # Import the module dynamically
            module = importlib.import_module(f".{module_name}", package=__name__)
            
            # Look for async functions that are likely tool functions
            # (functions that are async and don't start with underscore)
            for attr_name in dir(module):
                if not attr_name.startswith("_"):
                    attr = getattr(module, attr_name)
                    # Only include async functions (tool functions should be async)
                    # and exclude imported modules/classes
                    if (inspect.isfunction(attr) and 
                        inspect.iscoroutinefunction(attr) and
                        attr.__module__ == module.__name__):  # Only functions defined in this module
                        discovered_tools[attr_name] = attr
                        
        except ImportError as e:
            # Log the error but continue with other modules
            print(f"Warning: Could not import {module_name}: {e}")
            continue
    
    return discovered_tools

# Discover and import all tools
_tools = _discover_and_import_tools()

# Add all discovered tools to globals
for name, func in _tools.items():
    globals()[name] = func

# Define __all__ dynamically (common for module-style imports)
try:
    __all__ = sorted(_tools.keys())  # type: ignore[misc]
except Exception:
    # Fallback for static analysis tools
    __all__ = []
