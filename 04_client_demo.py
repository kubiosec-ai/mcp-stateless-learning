"""
04 — CLIENT DEMO: watch the difference  (FastMCP 4.0.0b1)
========================================================

This talks to the three servers over HTTP, exactly as a real client
would. Start the servers first (each in its own terminal):

    python 01_stateless_server.py      # -> http://127.0.0.1:8001/mcp
    python 02_stateful_global_server.py  # -> http://127.0.0.1:8002/mcp
    python 03_stateful_session_server.py # -> http://127.0.0.1:8003/mcp

then, in another terminal:

    python 04_client_demo.py

The servers are independent programs — this client is just ONE way to
use them. You can point any MCP client (Claude, an IDE, curl, another
SDK) at the same URLs.
"""

import asyncio

from fastmcp import Client

STATELESS_URL = "http://127.0.0.1:8001/mcp"
STATEFUL_GLOBAL_URL = "http://127.0.0.1:8002/mcp"
STATEFUL_SESSION_URL = "http://127.0.0.1:8003/mcp"


async def demo_stateless() -> None:
    print("\n=== 01 STATELESS: same input -> same output, no memory ===")
    async with Client(STATELESS_URL) as c:
        for _ in range(3):
            r = await c.call_tool("add", {"a": 2, "b": 3})
            print("  add(2, 3) ->", r.data)  # always 5


async def demo_stateful_global() -> None:
    print("\n=== 02 STATEFUL (global): the server remembers ===")
    async with Client(STATEFUL_GLOBAL_URL) as c:
        for _ in range(3):
            r = await c.call_tool("increment", {})
            print("  increment() ->", r.data)  # grows every run
        await c.call_tool("add_note", {"text": "buy milk"})
        await c.call_tool("add_note", {"text": "call Alice"})
        r = await c.call_tool("list_notes", {})
        print("  list_notes() ->", r.data)


async def demo_stateful_session() -> None:
    print("\n=== 03 STATEFUL (per-session over stateless protocol) ===")
    async with Client(STATEFUL_SESSION_URL) as c:
        # Two independent sessions, each with its own memory.
        a = (await c.call_tool("create_session", {})).data
        b = (await c.call_tool("create_session", {})).data
        print("  session A:", a)
        print("  session B:", b)

        for _ in range(3):
            r = await c.call_tool("increment", {"session_id": a})
            print("  A.increment() ->", r.data)  # 1, 2, 3

        r = await c.call_tool("increment", {"session_id": b})
        print("  B.increment() ->", r.data)  # 1 — B is independent

        r = await c.call_tool("increment", {"session_id": a})
        print("  A.increment() ->", r.data)  # 4 — A kept counting


async def main() -> None:
    try:
        await demo_stateless()
        await demo_stateful_global()
        await demo_stateful_session()
    except Exception as exc:  # noqa: BLE001
        print("\n[!] Could not reach a server:", exc)
        print("    Start the three servers first (see the header of this file).")
        return

    print("\nTakeaway:")
    print("  stateless          -> no memory; scales trivially")
    print("  stateful (global)  -> memory, but shared + process-local")
    print("  stateful (session) -> per-client memory via an explicit")
    print("                        handle, while the protocol stays stateless")


if __name__ == "__main__":
    asyncio.run(main())
