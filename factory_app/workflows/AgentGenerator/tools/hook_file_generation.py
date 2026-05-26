import logging
from typing import Any

logger = logging.getLogger(__name__)


def _read_system_message(agent: Any) -> str:
    return getattr(agent, "system_message", None) or getattr(agent, "_system_message", "") or ""


def _apply_system_message(agent: Any, message: str) -> None:
    updater = getattr(agent, "update_system_message", None)
    if callable(updater):
        updater(message)
    elif hasattr(agent, "_system_message"):
        agent._system_message = message
    else:
        agent._system_message = message
    agent._mozaiks_base_system_message = message

def inject_file_generation_instructions(agent, messages: list[dict[str, Any]]) -> None:
    """
    Injects comprehensive file generation best practices and compliance requirements
    into the agent's system message.
    """
    try:
        instructions = """
[FILE GENERATION INSTRUCTIONS]

**Best Practices**
- **Maintain Code Quality**:
    - Write clean, readable, and maintainable code following best practices for your chosen development framework.
    - Use the global logging configuration set up by the **Config/Middleware Agent** to log key actions, errors, and application state changes.
        - Log meaningful context for debugging (e.g., user IDs, operation status, error messages).
        - Use appropriate log levels (DEBUG, INFO, WARNING, ERROR, CRITICAL) to ensure logs are informative and actionable.
        - Avoid excessive or redundant logging to prevent clutter.
- **Documentation**:
    - At the top of every class, function, or method, include concise documentation (e.g., docstrings, JSDoc, or equivalent) describing its purpose, parameters, and return values where applicable.
- **Import Completeness**:
    - Ensure all required dependencies, utilities, or types are explicitly imported from their respective libraries or modules.
    - When importing methods from a class, always import the full class and call the method via the class name or an instantiated object. Do not import methods as if they were standalone functions unless explicitly exported as such.                
- **Error Prevention**:
    - Ensure that all referenced classes, functions, or variables are correctly defined and imported before use.
    - Avoid circular imports by properly structuring and decoupling modules.
    
**Critical Output Compliance Requirements:**
- **Output Format**: Provide **only** a valid JSON array of file objects. **No additional text, markdown, or commentary** is allowed.
- **Real Line Breaks**: Ensure all code and file content in the `content` fields is properly formatted with real line breaks (`\\n`).
- **No Markdown**: Do **not** wrap JSON output in code block markers (e.g., ```json).
- **Escaped Characters**: Only escape necessary characters as per JSON standards:
    - Use `\\"` for double quotes within strings.
    - Use `\\\\` for backslashes.
    - Use valid escape sequences like `\\n`, `\\t`, etc.
    - **For JavaScript files, always use double quotes (`"`) for strings instead of single quotes (`'`) so JSON parsing stays deterministic.**
    - **Do not use invalid escape sequences like `\\'` inside JSON output.**
- **Exact Formatting**: Generated files must be formatted for disk, without unnecessary modifications.
- **Code Documentation Formatting**: At the top of every class, function, or method, include concise documentation (e.g., docstrings, JSDoc, or equivalent) describing its purpose, parameters, and return values where applicable.
- **installRequirements Declaration**: When generating code, include an `installRequirements` field in the JSON object that:
    - Lists all external packages required for the code to run.
    - Includes any extras/optional extras required by the packages (e.g., `pydantic[email]` for `EmailStr`).
    - Explicitly lists dependencies for optional extras (e.g., `email-validator` for `pydantic[email]`).
    - Accounts for all imports, features, validation, and type-checking requirements.
    - Avoids listing local modules or files.
    - Prioritizes including dependencies to prevent runtime errors. Err on the side of over-inclusion.
    - If the techstack is python based, do not include bson as a dependency in your responses. If you need the ObjectId, rely on PyMongo or Motor’s internal bson module. Do not add "bson" to installRequirements.
- **Programmatic Parsing**: Output a valid JSON array that can be parsed without modification.
- **Exact Example**: Follow this JSON format:
```json
[
    {
        "filename": "relative/path/to/file.ext",
        "content": "Your file content goes here, including necessary imports. Make sure the logic in each file is comprehnesive. This code will be deployed in a Docker environment. Do not include placeholders or incomplete code—your output must be fully functional.",
        "installRequirements": ["some-package", "another-package"]
    },
    ...
]
```
"""
        # Check if instructions already exist to avoid duplication
        current = _read_system_message(agent)
        if "[FILE GENERATION INSTRUCTIONS]" in current:
             return

        # Append to system message
        new_message = f"{current}\n\n{instructions}" if current else instructions
        _apply_system_message(agent, new_message)
        logger.info(f"✓ Injected file generation instructions into {agent.name}")

    except Exception as e:
        logger.error(f"Error injecting file generation instructions for {agent.name}: {e}", exc_info=True)
