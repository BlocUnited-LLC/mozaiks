"""
Test E2B Sandbox Connection.
"""
import os
import sys
from dotenv import load_dotenv

# Load .env
load_dotenv()

try:
    from e2b_code_interpreter import Sandbox
except ImportError:
    print("Error: e2b_code_interpreter package is not installed.")
    print("Run: pip install e2b-code-interpreter")
    sys.exit(1)

def test_sandbox():
    api_key = os.getenv("E2B_API_KEY")
    if not api_key:
        print("Error: E2B_API_KEY not found in environment variables.")
        return

    print(f"Found E2B_API_KEY: {api_key[:4]}...{api_key[-4:]}")
    print("Initializing Sandbox...")

    try:
        # Create a sandbox
        with Sandbox.create() as sandbox:
            print("Sandbox created successfully!")
            
            # Run a simple command
            print("Running 'echo hello'...")
            result = sandbox.commands.run("echo hello")
            
            if result.stdout.strip() == "hello":
                print("✅ Success! Sandbox is working.")
            else:
                print(f"⚠️ Unexpected output: {result.stdout}")
                
    except Exception as e:
        print(f"❌ Failed to connect to E2B: {e}")

if __name__ == "__main__":
    test_sandbox()
