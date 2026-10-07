"""Offline ACP subprocess that probes its own and AG2's terminal boundary."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
from pathlib import Path
from typing import Any

import acp
from acp import schema

_EDITABLE_PATH = "app/ui/pages/Dashboard.jsx"
_EDITED_CONTENT = "export default function Dashboard() { return 1; }\n"
_CREATE_PATH = "app/ui/pages/New.jsx"
_CREATED_CONTENT = "export default function New() {}\n"
_TERMINAL_PROBE = """
import json, os, socket
s = socket.socket()
s.settimeout(0.5)
try:
    outbound = s.connect_ex(("1.1.1.1", 443)) == 0
except OSError:
    outbound = False
finally:
    s.close()
print(json.dumps({"host_secret_visible": "MOZAIKS_ACP_HOST_SECRET" in os.environ,
                  "outbound_reachable": outbound}))
"""


def _outbound_reachable() -> bool:
    with socket.socket() as connection:
        connection.settimeout(0.5)
        try:
            return connection.connect_ex(("1.1.1.1", 443)) == 0
        except OSError:
            return False


class ProofAgent:
    def __init__(self) -> None:
        self.client: Any = None
        self.cwd = Path("/workspace")

    def on_connect(self, client: Any) -> None:
        self.client = client

    async def initialize(self, **_kwargs: Any) -> schema.InitializeResponse:
        return schema.InitializeResponse(protocol_version=acp.PROTOCOL_VERSION)

    async def new_session(self, *, cwd: str, **_kwargs: Any) -> schema.NewSessionResponse:
        self.cwd = Path(cwd)
        return schema.NewSessionResponse(session_id="acp-container-proof")

    async def prompt(self, *, session_id: str, prompt: list[Any], **_kwargs: Any) -> schema.PromptResponse:
        instruction = "\n".join(str(getattr(block, "text", "")) for block in prompt)
        create_granted = f"Approved creation paths:\n- {_CREATE_PATH}" in instruction
        delete_granted = f"Approved deletion paths:\n- {_EDITABLE_PATH}" in instruction
        terminal = await self.client.create_terminal(
            session_id=session_id,
            command=sys.executable,
            args=["-c", _TERMINAL_PROBE],
            output_byte_limit=4096,
        )
        exit_status = await self.client.wait_for_terminal_exit(
            session_id=session_id, terminal_id=terminal.terminal_id
        )
        output = await self.client.terminal_output(
            session_id=session_id, terminal_id=terminal.terminal_id
        )
        await self.client.release_terminal(session_id=session_id, terminal_id=terminal.terminal_id)
        if exit_status.exit_code != 0 or output.truncated:
            raise RuntimeError("ACP terminal probe failed")
        terminal_result = json.loads(output.output)
        proof = {
            "adapter_host_secret_visible": "MOZAIKS_ACP_HOST_SECRET" in os.environ,
            "adapter_outbound_reachable": _outbound_reachable(),
            "baseline_file_visible": (self.cwd / "app/app.json").exists(),
            "read_only_test_visible": (self.cwd / "tests/test_dashboard.py").exists(),
            "host_sentinel_visible": Path("/workspace/.host_sentinel").exists(),
            "terminal": terminal_result,
        }
        inspection = self.cwd / "tests/test_dashboard.py"
        if inspection.exists():
            content = inspection.read_text(encoding="utf-8")
            if "# proof: edit-read-only" in content:
                await self.client.write_text_file(
                    session_id=session_id, path=str(inspection), content="changed inspection\n",
                )
            elif "# proof: delete-read-only" in content:
                inspection.unlink()
        if create_granted:
            await self.client.write_text_file(
                session_id=session_id, path=str(self.cwd / _CREATE_PATH), content=_CREATED_CONTENT,
            )
        if delete_granted:
            (self.cwd / _EDITABLE_PATH).unlink()
        if not create_granted and not delete_granted:
            await self.client.write_text_file(
                session_id=session_id, path=str(self.cwd / _EDITABLE_PATH), content=_EDITED_CONTENT,
            )
        await self.client.session_update(
            session_id=session_id,
            update=schema.AgentMessageChunk(
                content=schema.TextContentBlock(text=json.dumps(proof, sort_keys=True), type="text"),
                session_update="agent_message_chunk",
            ),
        )
        return schema.PromptResponse(stop_reason="end_turn")

    async def cancel(self, **_kwargs: Any) -> None:
        return None


if __name__ == "__main__":
    asyncio.run(acp.run_agent(ProofAgent()))
