"""Run a minimal live E2B connectivity check."""

import os

from dotenv import load_dotenv

load_dotenv()

try:
    from e2b_code_interpreter import Sandbox
except ImportError:
    print("Error: e2b_code_interpreter package is not installed.")
    print("Run: pip install e2b-code-interpreter")
    raise SystemExit(1) from None


def test_sandbox() -> bool:
    api_key = os.getenv("E2B_API_KEY")
    if not api_key:
        print("Error: E2B_API_KEY not found in environment variables.")
        return False

    print(f"Found E2B_API_KEY: {api_key[:4]}...{api_key[-4:]}")
    print("Initializing Sandbox...")

    try:
        with Sandbox.create() as sandbox:
            print("Sandbox created successfully!")
            print("Running 'echo hello'...")
            result = sandbox.commands.run("echo hello")
            if result.stdout.strip() == "hello":
                print("Success: E2B sandbox is working.")
                return True
            print(f"Unexpected output: {result.stdout}")
            return False
    except Exception as exc:
        print(f"Failed to connect to E2B: {exc}")
        return False


if __name__ == "__main__":
    raise SystemExit(0 if test_sandbox() else 1)
