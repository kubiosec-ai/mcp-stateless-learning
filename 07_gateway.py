"""
07: AN MCP GATEWAY (a controlled bridge)
========================================

A gateway is not a load balancer with extra features. It is an MCP *server*
to the client and an MCP *client* to the backends, so it can act on protocol
semantics instead of guessing from HTTP headers.

That single property fixes most of the problems in LOAD_BALANCER.md:

  * it can normalise protocol eras, so backends only ever see one
  * it is the natural home for a handle-to-node mapping
  * it can serialise writes per session, killing the lost-update race
    centrally without touching any backend
  * it is a policy choke point that actually understands JSON-RPC

Run the three demo servers first, then:

    python 07_gateway.py

    # everything through one endpoint:
    #   http://127.0.0.1:8000/mcp

Tools arrive namespaced by backend, so `add` on the calculator becomes
`calc_add` and there are no collisions between servers.

    python 07_gateway.py --demo     # start it and exercise it in one go
"""

from __future__ import annotations

import asyncio
import sys
import time
from collections import defaultdict

from fastmcp import Client, FastMCP
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.providers.proxy import ProxyProvider

# Backends this gateway federates. In a real deployment these would be
# service names or a pool per backend rather than fixed ports.
BACKENDS = {
    "calc": "http://127.0.0.1:8001/mcp",
    "notes": "http://127.0.0.1:8002/mcp",
    "sess": "http://127.0.0.1:8003/mcp",
}

# Tools the gateway refuses to forward, whatever the backend offers. A real
# policy would come from config and probably be per-tenant.
DENIED_TOOLS: set[str] = set()

# Rough per-tool call ceiling, to show where a limiter would sit.
MAX_CALLS_PER_TOOL = 1000


class GatewayPolicy(Middleware):
    """Audit log, allow-list and rate limit, in one place.

    This is the reason to run a gateway at all. It sees decoded JSON-RPC, so
    it can make decisions a WAF or load balancer cannot: which tool is being
    called, with which arguments, by whom.
    """

    def __init__(self) -> None:
        self.calls: dict[str, int] = defaultdict(int)

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        name = getattr(context.message, "name", "<unknown>")

        if name in DENIED_TOOLS:
            raise ValueError(f"tool {name!r} is not permitted through this gateway")

        self.calls[name] += 1
        if self.calls[name] > MAX_CALLS_PER_TOOL:
            raise ValueError(f"rate limit exceeded for {name!r}")

        started = time.perf_counter()
        try:
            result = await call_next(context)
        except Exception as exc:
            print(f"[audit] {name} FAILED {type(exc).__name__}", flush=True)
            raise
        elapsed = (time.perf_counter() - started) * 1000
        print(f"[audit] {name} ok {elapsed:.0f}ms (call #{self.calls[name]})", flush=True)
        return result


class SessionSerialiser(Middleware):
    """Serialise calls that carry the same `session_id`.

    Aimed at the read-modify-write race demonstrated in PROTOCOL_TRACE.md,
    where two replicas sharing a session store silently lose updates. Holding
    a lock per session id means only one call for a given session is in flight
    at a time, so the interleaving that loses writes cannot occur.

    Honest status: the race itself was reproduced at the store level (20
    concurrent increments across two replicas gave 17). An attempt to
    reproduce it *through* this gateway did not trigger it, most likely
    because the proxy pooled onto a single backend connection, so this
    middleware is reasoned-and-designed rather than measured end to end.
    Verify it against your own topology before relying on it.

    Two real costs. Calls sharing a session no longer run in parallel. And in
    a multi-instance gateway this in-process lock is not enough: it has to
    become a distributed lock, or a session has to route consistently to one
    gateway instance, which is affinity again one layer up.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, sid: str) -> asyncio.Lock:
        if sid not in self._locks:
            self._locks[sid] = asyncio.Lock()
        return self._locks[sid]

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        args = getattr(context.message, "arguments", None) or {}
        sid = args.get("session_id")
        if not isinstance(sid, str):
            return await call_next(context)
        async with self._lock_for(sid):
            return await call_next(context)


def build_gateway() -> FastMCP:
    gw = FastMCP(
        "mcp-gateway",
        instructions=(
            "Federates several MCP servers behind one endpoint. Tools are "
            "namespaced by backend."
        ),
        middleware=[GatewayPolicy(), SessionSerialiser()],
    )

    for namespace, url in BACKENDS.items():
        # cache_ttl caches the backend's component list, which removes the
        # per-connection tools/list refetch. The cost is staleness: a tool
        # added to a backend stays invisible until the entry expires.
        gw.add_provider(
            ProxyProvider(lambda u=url: Client(u), cache_ttl=300),
            namespace=namespace,
        )
    return gw


async def _demo() -> None:
    """Drive the gateway to show federation and session routing working."""
    await asyncio.sleep(2)
    async with Client("http://127.0.0.1:8000/mcp") as c:
        tools = [t.name for t in await c.list_tools()]
        print("\n  gateway exposes:")
        for t in tools:
            print(f"    {t}")

        print("\n  calling through the gateway:")
        r = await c.call_tool("calc_add", {"a": 2, "b": 3})
        print(f"    calc_add(2,3) -> {r.data}")

        sid = (await c.call_tool("sess_create_session", {})).data
        for _ in range(3):
            r = await c.call_tool("sess_increment", {"session_id": sid})
        print(f"    sess_increment x3 -> {r.data}  (session state intact through the proxy)")


if __name__ == "__main__":
    gateway = build_gateway()

    if "--demo" in sys.argv:
        async def run_both() -> None:
            server = asyncio.create_task(
                gateway.run_async(
                    transport="http", host="127.0.0.1", port=8000,
                    stateless_http=True, show_banner=False,
                )
            )
            try:
                await _demo()
            finally:
                server.cancel()
        asyncio.run(run_both())
    else:
        print("[info] gateway on http://127.0.0.1:8000/mcp")
        print(f"[info] backends: {', '.join(BACKENDS)}")
        gateway.run(transport="http", host="127.0.0.1", port=8000, stateless_http=True)
