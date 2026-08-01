"""
05 — COMPATIBILITY CHECKER
==========================

Answers one question: *can this SDK talk to these servers?*

MCP is mid-migration. The 2026-07-28 spec made the protocol stateless, and
the official SDKs shipped a new major version for it — but several popular
agent frameworks are still pinned to the previous generation. This script
reports which client library it found, which MCP SDK version is installed,
and whether it can actually list and call tools on each server.

It tries these client backends in order and uses the first one available:

  1. fastmcp.Client                       (FastMCP, modern era)
  2. agents.mcp.MCPServerStreamableHttp   (OpenAI Agents SDK, legacy era)
  3. mcp.client.streamable_http           (official SDK, either era)

Start the servers first, then:

    python 05_compat_check.py

To prove cross-era compatibility, run it a second time from an environment
holding a legacy-era SDK:

    python -m venv /tmp/venv_legacy
    /tmp/venv_legacy/bin/pip install openai-agents   # pins mcp<2
    /tmp/venv_legacy/bin/python 05_compat_check.py

Both runs should pass against the same running servers. That is the whole
backward-compatibility story, demonstrated rather than asserted.
"""

import asyncio
import importlib.metadata as md

TARGETS = [
    ("01 stateless", "http://127.0.0.1:8001/mcp"),
    ("02 stateful-global", "http://127.0.0.1:8002/mcp"),
    ("03 stateful-session", "http://127.0.0.1:8003/mcp"),
]


def version_of(pkg: str) -> str:
    try:
        return md.version(pkg)
    except Exception:
        return "not installed"


def report_environment() -> None:
    print("Installed MCP-related packages")
    print("-" * 60)
    for pkg in ("mcp", "fastmcp", "openai-agents", "agent-framework-core"):
        print(f"  {pkg:22s} {version_of(pkg)}")

    mcp_ver = version_of("mcp")
    if mcp_ver != "not installed":
        major = mcp_ver.split(".")[0]
        era = (
            "modern (2026-07-28, stateless core)"
            if major >= "2"
            else "legacy (2025-11-25 handshake)"
        )
        print(f"\n  -> official SDK major v{major} => {era}")
    print()


# --- client backends -------------------------------------------------------


async def try_fastmcp(url: str):
    from fastmcp import Client

    async with Client(url) as c:
        tools = [t.name for t in await c.list_tools()]
        add = None
        if "add" in tools:
            add = (await c.call_tool("add", {"a": 2, "b": 3})).data
        return tools, add


async def try_openai_agents(url: str):
    from agents.mcp import MCPServerStreamableHttp

    async with MCPServerStreamableHttp(
        params={"url": url}, client_session_timeout_seconds=20
    ) as s:
        tools = [t.name for t in await s.list_tools()]
        add = None
        if "add" in tools:
            add = (await s.call_tool("add", {"a": 2, "b": 3})).content[0].text
        return tools, add


async def try_official_sdk(url: str):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(url) as (read, write, *_):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = [t.name for t in (await session.list_tools()).tools]
            add = None
            if "add" in tools:
                add = (await session.call_tool("add", {"a": 2, "b": 3})).content[0].text
            return tools, add


BACKENDS = [
    ("fastmcp.Client", "fastmcp", try_fastmcp),
    ("openai-agents (agents.mcp)", "agents.mcp", try_openai_agents),
    ("official mcp SDK", "mcp.client.streamable_http", try_official_sdk),
]


def pick_backend():
    import importlib.util

    for label, module, fn in BACKENDS:
        if importlib.util.find_spec(module.split(".")[0]) is not None:
            try:
                importlib.import_module(module)
            except Exception:
                continue
            return label, fn
    return None, None


async def main() -> None:
    report_environment()

    label, fn = pick_backend()
    if fn is None:
        print("No supported MCP client library found.")
        return

    print(f"Using client backend: {label}")
    print("-" * 60)

    ok = 0
    for name, url in TARGETS:
        try:
            tools, add = await fn(url)
            extra = f"  add(2,3)={add}" if add is not None else ""
            print(f"  [OK]   {name:20s} {len(tools)} tools{extra}")
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  [FAIL] {name:20s} {type(exc).__name__}: {str(exc)[:60]}")

    print("-" * 60)
    print(f"{ok}/{len(TARGETS)} servers reachable.")
    if ok == 0:
        print("Are the servers running? See the README.")


if __name__ == "__main__":
    asyncio.run(main())
