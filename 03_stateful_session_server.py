"""
03: STATEFUL, PER-SESSION, the FastMCP 4 way  (FastMCP 4.0.0b1)
===============================================================

This is the important one. It shows how to keep real state PER
SESSION while running on the *stateless* MCP protocol
(the 2026-07-28 spec).

The trick is the "explicit handle" pattern:

  1. The client calls `create_session()` and gets back an
     unguessable session id (a handle).
  2. The client passes that id as `session_id=...` on later calls.
  3. Each request can land on ANY server replica, the id is all
     that's needed to find that session's state in the shared store.

So the state is explicit and visible (it's just an argument), the
transport stays stateless (no sticky sessions), and different
clients get different, isolated state.

Compared with example 02:
  * per-session instead of one global blob, and
  * backed by the server's `session_state_store` (in-memory here,
    but swap in Redis/etc. and every replica agrees).

Handles should also be *legible*. `create_session()` hands back a bare
uuid, which a model cannot choose between. `start_notebook(name)` shows the
better shape: return the id together with a name and a summary, so the model
picks by meaning and audit logs read properly.

Key pieces (all real FastMCP 4.0.0b1 API):
  * `mcp.add_provider(SessionProvider())`  -> registers the
    `create_session()` and `end_session(session_id)` tools.
  * a tool argument typed `session_id: SessionId`.
  * `await get_session(session_id)` -> a Session with async
    `.get()` / `.set()` / `.delete()` / `.clear()`.

Run it:
    pip install "fastmcp==4.0.0b1"
    python 03_stateful_session_server.py
    # serves on http://127.0.0.1:8003/mcp
"""

from fastmcp import FastMCP
from fastmcp.server.sessions import (
    SessionId,
    SessionProvider,
    create_session,
    get_session,
)

mcp = FastMCP("per-session-notebook")

# Register the create_session() / end_session() lifecycle tools.
mcp.add_provider(SessionProvider())


@mcp.tool
async def start_notebook(name: str) -> dict:
    """Create a session and return a handle the MODEL can actually use.

    `create_session()` (registered by SessionProvider) returns a bare uuid
    like "32fd291f-e091-4210-a8af-8b029fb6b1e8". That is fine for a machine
    and useless for a model: with three of them in context, nothing in the
    string says which notebook is which, so the model has to guess.

    A handle should carry enough context to be chosen sensibly. Returning
    the id together with a name and a summary means the model can say "the
    shopping one" instead of pattern-matching hex. It also makes audit logs
    readable, because the tool call records which notebook was touched.

    This is the same idea as the id itself: make the state explicit and
    legible rather than hidden.
    """
    session_id = await create_session()
    session = await get_session(session_id)
    await session.set("name", name)
    return {"session_id": session_id, "name": name, "facts": 0}


@mcp.tool
async def describe(session_id: SessionId) -> dict:
    """Return a readable summary of a session, not just its raw state."""
    session = await get_session(session_id)
    facts = await session.get("facts", default=[])
    return {
        "session_id": session_id,
        "name": await session.get("name", default="(unnamed)"),
        "facts": len(facts),
        "count": await session.get("count", default=0),
    }


@mcp.tool
async def increment(session_id: SessionId) -> int:
    """Increment a counter that belongs to THIS session only."""
    session = await get_session(session_id)
    count = await session.get("count", default=0)
    count += 1
    await session.set("count", count)
    return count


@mcp.tool
async def remember(session_id: SessionId, fact: str) -> str:
    """Store a fact in THIS session's memory."""
    session = await get_session(session_id)
    facts = await session.get("facts", default=[])
    facts.append(fact)
    await session.set("facts", facts)
    return f"Remembered ({len(facts)} total): {fact!r}"


@mcp.tool
async def recall(session_id: SessionId) -> list[str]:
    """Return everything remembered in THIS session."""
    session = await get_session(session_id)
    return await session.get("facts", default=[])


if __name__ == "__main__":
    import sys

    # Note: the default session_state_store is in-memory and
    # process-local. For a load-balanced deployment, pass a shared
    # store, e.g. FastMCP("name", session_state_store=<AsyncKeyValue>),
    # and every replica will see the same session state.
    if "--stdio" in sys.argv:
        # stdio mode, for the MCP Inspector, Claude Desktop, IDEs.
        mcp.run()
    else:
        mcp.run(transport="http", host="127.0.0.1", port=8003)
