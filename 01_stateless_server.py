"""
01: STATELESS MCP SERVER  (FastMCP 4.0.0b1)
============================================

A *stateless* server keeps NO memory between tool calls.
Every call is fully described by its own arguments, and the
answer depends only on those arguments, never on what happened
before.

    Mental model:   f(input) -> output      (a pure function)

Why it matters:
  * Any call can be handled by any worker / replica.
  * You can scale horizontally behind a plain round-robin load
    balancer, no "sticky sessions", no shared session store.
  * Restarting the server loses nothing.

This is the direction the MCP spec itself took in the 2026-07-28
release: the *protocol* is now stateless at its core. Below we
also pass `stateless_http=True` so the HTTP transport creates no
per-client session either.

Run it:
    pip install "fastmcp==4.0.0b1"
    python 01_stateless_server.py
    # serves on http://127.0.0.1:8001/mcp
"""

from fastmcp import FastMCP

mcp = FastMCP("stateless-calculator")


@mcp.tool
def add(a: float, b: float) -> float:
    """Add two numbers. The result depends ONLY on a and b."""
    return a + b


@mcp.tool
def multiply(a: float, b: float) -> float:
    """Multiply two numbers. Same inputs -> always the same output."""
    return a * b


@mcp.tool
def celsius_to_fahrenheit(celsius: float) -> float:
    """Convert a temperature. No hidden memory involved."""
    return celsius * 9 / 5 + 32


if __name__ == "__main__":
    import sys

    if "--stdio" in sys.argv:
        # stdio: the server talks over stdin/stdout. This is what the
        # MCP Inspector launches by default, and what Claude Desktop
        # and most IDE integrations expect.
        mcp.run()
    else:
        # stateless_http=True -> the HTTP layer keeps NO per-client
        #   session; every request is independent.
        #
        # Pass --with-sessions to run the exact same tools WITH HTTP
        # sessions enabled, if you want to compare the two side by side.
        stateless = "--with-sessions" not in sys.argv
        print(
            f"[info] HTTP on :8001  stateless_http={stateless}"
            + ("" if stateless else "  (sessions enabled)")
        )
        mcp.run(
            transport="http",
            host="127.0.0.1",
            port=8001,
            stateless_http=stateless,
        )
