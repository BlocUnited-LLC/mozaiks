"""Scripted live-worker stand-in for the opt-in repository Docker gates.

The gate tests build this into an image derived from the real live worker
image, so it runs with that image's filesystem and environment under the
exact container and network settings of the live transport. It never calls a
model provider. It reads the same scoped stdin request as the real worker;
``raw_user_request`` is a JSON script naming the gate mode.

With ``probe`` set, it first records what an untrusted agent could reach and
read: direct connections to the internet, host, metadata and egress-bridge
addresses, the gateway's own responses, its routing table, and its process
environment. An ``update`` turn returns that report as the edited file's
content, so the host receives it only through the normal verified archive.
Other modes return deliberately invalid or failing results.
"""

from __future__ import annotations

import base64
import errno
import http.client
import json
import os
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from mozaiksai.control_plane.repository_patch import _EMPTY_REPOSITORY_ARCHIVE
from mozaiksai.core.semantics.archive import ArchiveEntry, build_deterministic_archive

_GATEWAY_HOST = "model-gateway"
_GATEWAY_PORT = 8765
_PROBE_TIMEOUT = 3.0
_MODEL_PATHS = {"claude_code": "/v1/messages", "codex": "/v1/responses"}
_NO_ROUTE = (errno.ENETUNREACH, errno.EHOSTUNREACH, errno.EADDRNOTAVAIL)


def _tcp(host: str, port: int) -> str:
    try:
        addresses = socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except OSError:
        return "dns_failed"
    outcome = "unreachable"
    for family, kind, proto, _name, address in addresses:
        connection = socket.socket(family, kind, proto)
        connection.settimeout(_PROBE_TIMEOUT)
        try:
            connection.connect(address)
            return "connected"
        except ConnectionRefusedError:
            return "refused"
        except TimeoutError:
            outcome = "timeout"
        except OSError as exc:
            if exc.errno not in _NO_ROUTE:
                return f"error:{exc.errno}"
        finally:
            connection.close()
    return outcome


def _udp_dns(server: str) -> str:
    # A minimal DNS question; any reply proves a direct UDP route.
    query = bytes.fromhex("abcd01000001000000000000076578616d706c6503636f6d0000010001")
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.settimeout(_PROBE_TIMEOUT)
    try:
        probe.sendto(query, (server, 53))
        probe.recvfrom(512)
        return "connected"
    except TimeoutError:
        return "timeout"
    except OSError as exc:
        if exc.errno in _NO_ROUTE:
            return "unreachable"
        if exc.errno == errno.ECONNREFUSED:
            return "refused"
        return f"error:{exc.errno}"
    finally:
        probe.close()


def _gateway(
    method: str, path: str, *, token: str | None, body: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    connection = http.client.HTTPConnection(_GATEWAY_HOST, _GATEWAY_PORT, timeout=10)
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    encoded = json.dumps(body).encode("utf-8") if body is not None else None
    try:
        connection.request(method, path, body=encoded, headers=headers)
        response = connection.getresponse()
        raw = response.read(65_536)
        try:
            return response.status, json.loads(raw)
        except ValueError:
            return response.status, None
    except OSError as exc:
        return -1, f"error:{exc.errno}"
    finally:
        connection.close()


def _routes() -> dict[str, Any]:
    with open("/proc/net/route", encoding="ascii") as table:
        rows = [line.split() for line in table.readlines()[1:] if line.strip()]
    return {"count": len(rows), "default_route": any(row[1] == "00000000" for row in rows)}


def _private_bridge_candidate() -> str | None:
    """Return the private subnet's first address unless a known peer holds it."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        gateway_address = socket.gethostbyname(_GATEWAY_HOST)
        probe.connect((gateway_address, 9))
        own = probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()
    candidate = ".".join([*own.split(".")[:3], "1"])
    return None if candidate in {own, gateway_address} else candidate


def _read_text(path: str) -> str | None:
    try:
        with open(path, "rb") as source:
            return source.read(1_048_576).replace(b"\0", b"\n").decode("utf-8", errors="replace")
    except OSError:
        return None


def _probe(token: str, adapter: str, model: str) -> dict[str, Any]:
    path = _MODEL_PATHS[adapter]
    message: dict[str, Any] = (
        {"model": model, "max_tokens": 16, "messages": [{"role": "user", "content": "gate"}]}
        if adapter == "claude_code" else {"model": model, "input": "gate", "max_output_tokens": 16}
    )
    gateway = {
        "approved_call": _gateway("POST", path, token=token, body=message)[0],
        "wrong_model": _gateway("POST", path, token=token, body={**message, "model": "unapproved-model"})[0],
        "wrong_token": _gateway("POST", path, token="x" * 43, body=message)[0],
        "no_token": _gateway("POST", path, token=None, body=message)[0],
        "unknown_route": _gateway("POST", "/v1/files", token=token, body=message)[0],
        "get_method": _gateway("GET", path, token=token)[0],
        "targets_without_token": _gateway("GET", "/gate/targets", token=None)[0],
    }
    status, targets = _gateway("GET", "/gate/targets", token=token)
    gateway["targets_status"] = status
    targets = targets if isinstance(targets, dict) else {}
    tcp_targets = {
        "internet_ip_https": ("1.1.1.1", 443),
        "internet_ip_dns_tcp": ("8.8.8.8", 53),
        "internet_name_https": ("example.com", 443),
        "host_docker_internal_http": ("host.docker.internal", 80),
        "host_docker_internal_https": ("host.docker.internal", 443),
        "gateway_docker_internal_http": ("gateway.docker.internal", 80),
        "docker_desktop_host_http": ("192.168.65.254", 80),
        "default_bridge_gateway_http": ("172.17.0.1", 80),
        "metadata_http": ("169.254.169.254", 80),
    }
    egress_gateway = targets.get("egress_gateway_ip")
    gateway_egress = targets.get("gateway_egress_ip")
    if isinstance(egress_gateway, str):
        tcp_targets["egress_bridge_gateway_http"] = (egress_gateway, 80)
        tcp_targets["egress_bridge_gateway_dns_tcp"] = (egress_gateway, 53)
    if isinstance(gateway_egress, str):
        tcp_targets["gateway_egress_address"] = (gateway_egress, _GATEWAY_PORT)
    private_bridge = _private_bridge_candidate()
    if private_bridge is not None:
        # A non-isolated bridge would hold the subnet's first address on the
        # host. An isolated bridge has none, so a peer container may use it.
        tcp_targets["private_bridge_first_address_http"] = (private_bridge, 80)
        tcp_targets["private_bridge_first_address_dns_tcp"] = (private_bridge, 53)
    with ThreadPoolExecutor(max_workers=16) as pool:
        tcp = {name: pool.submit(_tcp, host, port) for name, (host, port) in tcp_targets.items()}
        udp = {
            name: pool.submit(_udp_dns, server)
            for name, server in (("internet_dns_udp_8888", "8.8.8.8"), ("internet_dns_udp_1111", "1.1.1.1"))
        }
        allowed = pool.submit(_tcp, _GATEWAY_HOST, _GATEWAY_PORT)
        forbidden = {name: future.result() for name, future in {**tcp, **udp}.items()}
        gateway_reach = allowed.result()
    return {
        "forbidden": forbidden,
        "forbidden_targets": {name: f"{host}:{port}" for name, (host, port) in tcp_targets.items()},
        "gateway_tcp": gateway_reach,
        "gateway": gateway,
        "targets": targets,
        "routes": _routes(),
        "environment": dict(os.environ),
        "process": {
            "self_cmdline": _read_text("/proc/self/cmdline"),
            "pid1_cmdline": _read_text("/proc/1/cmdline"),
            "pid1_environ": _read_text("/proc/1/environ"),
            "uid": os.getuid(),
        },
    }


def main() -> None:
    request = json.loads(sys.stdin.buffer.read(20_971_521))
    live = request["live"]
    script = json.loads(request["raw_user_request"])
    mode = script["gate_mode"]
    if mode == "crash":
        raise SystemExit(3)
    if mode == "hang":
        time.sleep(3600)
    if mode == "malformed":
        sys.stdout.write("not a repository turn result\n")
        return
    report = _probe(live["job_token"], live["adapter"], live["model"]) if script.get("probe") else None
    files: dict[str, str] = request["files"]
    read_only: dict[str, str] = request["read_only_files"]
    entries = dict(files)
    changes: list[dict[str, Any]] = []
    if mode in {"update", "read_only_mutation", "out_of_scope", "token_copy"}:
        path = sorted(files)[0]
        if report is not None:
            content = json.dumps(report, sort_keys=True) + "\n"
        elif mode == "token_copy":
            content = files[path] + live["job_token"] + "\n"
        else:
            content = files[path] + "gate update\n"
        entries[path] = content
        changes.append({"path": path, "op": "update", "content": content})
        if mode == "read_only_mutation":
            target = sorted(read_only)[0]
            entries[target] = read_only[target] + "mutated\n"
            changes.append({"path": target, "op": "update", "content": entries[target]})
        if mode == "out_of_scope":
            entries["unapproved/gate.txt"] = "out of scope\n"
            changes.append({"path": "unapproved/gate.txt", "op": "create", "content": "out of scope\n"})
    elif mode == "create":
        for path in request["create_paths"]:
            entries[path] = "created by gate\n"
            changes.append({"path": path, "op": "create", "content": entries[path]})
    elif mode == "delete":
        for path in request["delete_paths"]:
            entries.pop(path)
            changes.append({"path": path, "op": "delete", "content": None})
    else:
        raise SystemExit(4)
    archive = (
        build_deterministic_archive(
            ArchiveEntry(path=path, content=content.encode("utf-8")) for path, content in entries.items()
        )
        if entries else _EMPTY_REPOSITORY_ARCHIVE
    )
    output = {
        "proposal": {
            "proposal_id": "gate", "provider_id": "gate", "status": "completed",
            "summary": "gate", "rationale": "gate",
            "owned_paths": [change["path"] for change in changes],
            "changed_files": changes,
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        },
        "workspace_archive_base64": base64.b64encode(archive).decode("ascii"),
    }
    time.sleep(float(script.get("hold_seconds", 0)))
    sys.stdout.write(json.dumps(output, separators=(",", ":")))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
