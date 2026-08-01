"""
02 — STATEFUL MCP SERVER, the simple (global) way  (FastMCP 4.0.0b1)
====================================================================

A *stateful* server REMEMBERS things between tool calls. The answer
to a call can depend on previous calls, not just on the current
arguments.

    Mental model:   the server has memory.
                    call increment() 3 times -> you get 1, 2, 3

Here the state lives in ordinary module-level Python variables held
in the server process. This is the SIMPLEST possible illustration
of "the server remembers" — perfect for a first lesson.

But notice the trade-offs (this is the teaching point):
  * The state is GLOBAL — every client shares the same counter.
  * The state is PROCESS-LOCAL — restarting the server wipes it,
    and a second replica would have its own separate copy.
  * Behind a load balancer you'd need session affinity or a shared
    store to make this behave correctly.

Example 03 shows the FastMCP 4 way that fixes both problems
(per-user, and backed by a configurable shared store) while still
running on the stateless protocol.

Run it:
    pip install "fastmcp==4.0.0b1"
    python 02_stateful_global_server.py
    # serves on http://127.0.0.1:8002/mcp
"""

from fastmcp import FastMCP

mcp = FastMCP("stateful-notebook")

# ---- In-process state: THIS is what makes the server stateful ----
_counter = 0
_notes: list[str] = []
# ------------------------------------------------------------------


@mcp.tool
def increment() -> int:
    """Increase a shared counter and return its new value.

    Call it repeatedly and the number keeps growing: the server is
    remembering the previous value between calls.
    """
    global _counter
    _counter += 1
    return _counter


@mcp.tool
def get_counter() -> int:
    """Read the current counter without changing it."""
    return _counter


@mcp.tool
def add_note(text: str) -> str:
    """Store a note. It stays in memory for later calls."""
    _notes.append(text)
    return f"Stored note #{len(_notes)}: {text!r}"


@mcp.tool
def list_notes() -> list[str]:
    """Return every note stored so far in this server process."""
    return list(_notes)


if __name__ == "__main__":
    import sys

    if "--stdio" in sys.argv:
        # stdio mode — for the MCP Inspector, Claude Desktop, IDEs.
        mcp.run()
    else:
        # A normal HTTP server. The state above is shared across ALL
        # clients because it's a plain module-level variable — a
        # deliberately obvious form of server-side state for teaching.
        mcp.run(transport="http", host="127.0.0.1", port=8002)
