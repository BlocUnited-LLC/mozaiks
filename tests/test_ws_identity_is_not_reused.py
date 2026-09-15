"""Connection identity must never be reused.

``ws_id`` was ``id(websocket)`` — a memory address, unique only among *live*
objects. CPython hands the same address to a new object once the previous one
is collected, and under a reconnect that is exactly what happens: a socket
closes, is collected, and the replacement lands on the same address.

``_cleanup_connection`` compares ``ws_id`` to decide whether a late cleanup
still owns the slot. With a recycled address the departing socket's cleanup
compares equal to the new owner's and tears down a live connection — defeating
the very guard that exists to prevent it. Seen in a live run as one ws_id
appearing under two different chats. See #576.
"""

from __future__ import annotations

import pytest

from mozaiksai.core.transport.simple_transport import SimpleTransport


def _transport() -> SimpleTransport:
    transport = SimpleTransport.__new__(SimpleTransport)
    transport._ws_id_counter = 0
    transport._ws_id_lock = __import__("threading").Lock()
    return transport


def test_identities_are_unique_even_when_addresses_are_recycled() -> None:
    transport = _transport()

    class _Socket:
        pass

    first = _Socket()
    first_address = id(first)
    first_id = transport._next_ws_id()

    del first
    # A replacement object may land on the freed address; identity must not.
    replacements = [_Socket() for _ in range(200)]
    recycled = any(id(obj) == first_address for obj in replacements)
    second_id = transport._next_ws_id()

    assert second_id != first_id, (
        "a reconnect reused a connection identity"
        + (" (and the address was genuinely recycled)" if recycled else "")
    )


def test_identity_is_monotonic() -> None:
    transport = _transport()
    issued = [transport._next_ws_id() for _ in range(50)]
    assert issued == sorted(issued), "identities must increase"
    assert len(set(issued)) == len(issued), "identities must never repeat"


def test_the_takeover_guard_can_distinguish_two_connections() -> None:
    """The guard's whole job: tell a departing socket from the new owner."""
    transport = _transport()
    departing = transport._next_ws_id()
    arriving = transport._next_ws_id()

    # This is the comparison _cleanup_connection makes.
    assert departing != arriving, (
        "the guard cannot distinguish the sockets, so a late cleanup would "
        "tear down the live connection"
    )


@pytest.mark.parametrize("workers", [8])
def test_concurrent_allocation_never_collides(workers: int) -> None:
    """Connections are accepted from multiple threads."""
    from concurrent.futures import ThreadPoolExecutor

    transport = _transport()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        issued = list(pool.map(lambda _i: transport._next_ws_id(), range(400)))

    assert len(set(issued)) == len(issued), "concurrent accepts collided"
