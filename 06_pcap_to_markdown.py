"""
06: PCAP TO MARKDOWN
====================

Turns a packet capture of the demo traffic into a readable protocol walkthrough
(`PROTOCOL_TRACE.md`), so students can see the actual bytes MCP puts on the
wire instead of taking the SDK's word for it.

For every HTTP exchange it emits the request line, the interesting headers, and
the JSON-RPC body pretty-printed. Server-Sent-Event framing is unwrapped so the
payload reads as plain JSON.

Capture the traffic first (loopback, all three demo ports):

    sudo tcpdump -i lo0 -s 0 -w mcp_stateless.pcap \\
        'tcp port 8001 or tcp port 8002 or tcp port 8003'

    # in another terminal
    python 04_client_demo.py

Then:

    python 06_pcap_to_markdown.py mcp_stateless.pcap

Requires tshark (`brew install wireshark` on macOS).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

SERVERS = {
    "8001": ("01 stateless", "stateless_http=True, no session at all"),
    "8002": ("02 stateful (global)", "one counter shared by every client"),
    "8003": ("03 stateful (per-session)", "explicit session-id handles"),
}

# Headers worth showing. Everything else is noise for teaching purposes.
KEEP_HEADERS = (
    "host",
    "content-type",
    "accept",
    "mcp-protocol-version",
    "mcp-method",
    "mcp-name",
    "mcp-session-id",
    "content-length",
)

# Prefixes to keep as well. `Mcp-Param-*` carries arguments that a tool opted
# into mirroring as headers, so a gateway can route on them.
KEEP_HEADER_PREFIXES = ("mcp-param-",)


def tshark(pcap: str, display_filter: str, fields: list[str]) -> list[list[str]]:
    cmd = ["tshark", "-r", pcap, "-Y", display_filter, "-T", "fields"]
    for f in fields:
        cmd += ["-e", f]
    cmd += ["-E", "separator=\x01", "-E", "occurrence=a", "-E", "aggregator=\x02"]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    rows = []
    for line in out.splitlines():
        if line.strip():
            rows.append(line.split("\x01"))
    return rows


def decode_body(hex_blob: str) -> str:
    if not hex_blob:
        return ""
    # tshark may aggregate multiple data chunks
    raw = b"".join(bytes.fromhex(h) for h in hex_blob.split("\x02") if h)
    return raw.decode("utf-8", errors="replace")


def unwrap_sse(body: str) -> str:
    """Strip `event: message / data: {...}` framing, keeping the JSON."""
    if "data:" not in body:
        return body
    chunks = re.findall(r"^data:\s*(.+)$", body, flags=re.MULTILINE)
    return "\n".join(chunks) if chunks else body


def pretty_json(body: str) -> str:
    body = unwrap_sse(body).strip()
    if not body:
        return ""
    out = []
    for piece in body.splitlines():
        piece = piece.strip()
        if not piece:
            continue
        try:
            out.append(json.dumps(json.loads(piece), indent=2))
        except json.JSONDecodeError:
            out.append(piece)
    return "\n".join(out)


def clean_headers(raw: str) -> list[str]:
    lines = []
    for h in raw.split("\x02"):
        h = h.replace("\\r\\n", "").strip()
        if not h:
            continue
        key = h.split(":")[0].strip().lower()
        if key in KEEP_HEADERS or key.startswith(KEEP_HEADER_PREFIXES):
            lines.append(h)
    return lines


def summarise(body: str) -> str:
    """One-line description of a JSON-RPC message, for the section heading."""
    try:
        msg = json.loads(unwrap_sse(body).strip().splitlines()[0])
    except Exception:
        return ""
    if "method" in msg:
        name = msg.get("params", {}).get("name")
        return f"{msg['method']}" + (f" ({name})" if name else "")
    if "result" in msg:
        return "result"
    if "error" in msg:
        return "error"
    return ""


def build(pcap: str, out_path: str) -> None:
    reqs = tshark(
        pcap,
        "http.request",
        ["frame.number", "tcp.stream", "tcp.dstport", "http.request.method",
         "http.request.uri", "http.request.line", "http.file_data"],
    )
    resps = tshark(
        pcap,
        "http.response",
        ["frame.number", "tcp.stream", "http.response.code", "http.request_in",
         "http.response.line", "http.file_data"],
    )

    by_request = {}
    for r in resps:
        if len(r) >= 6 and r[3]:
            by_request[r[3]] = r

    doc: list[str] = []
    doc.append("# MCP on the wire: a packet-level walkthrough\n")
    doc.append(
        "Generated from `mcp_stateless.pcap` by `06_pcap_to_markdown.py`. "
        "This is the traffic produced by `04_client_demo.py`, so every exchange "
        "below corresponds to something in that script.\n"
    )

    doc.append("## What to notice\n")
    doc.append(
        "**1. There is no handshake.** The very first message is "
        "`server/discover`, and the client is already sending real requests "
        "immediately after. The old `initialize` / `initialized` exchange is "
        "gone.\n"
    )
    doc.append(
        "**2. No session header anywhere.** Search the whole trace for "
        "`Mcp-Session-Id` and you will not find it. The 2026-07-28 protocol "
        "removed it, which is what lets any replica answer any request.\n"
    )
    doc.append(
        "**3. Every request is self-describing.** Each one repeats its "
        "`_meta` block with `protocolVersion`, `clientInfo` and "
        "`clientCapabilities`. That is the cost of statelessness: a little more "
        "on the wire, in exchange for no server-side memory.\n"
    )
    doc.append(
        "**4. Routing headers mirror the body.** `mcp-protocol-version`, "
        "`mcp-method` and `mcp-name` (the tool being called) appear as HTTP "
        "headers, so a gateway or load balancer can route on them without "
        "parsing the JSON-RPC payload. See the load-balancing note below for "
        "what is deliberately *not* there.\n"
    )
    doc.append(
        "**5. State is a plain argument.** In the `03` section, `session_id` "
        "travels inside `arguments` like any other parameter. That is the whole "
        "explicit-handle pattern, visible in the bytes. Watch one session count "
        "1, 2, 3 while a second session independently starts at 1.\n"
    )

    doc.append("## Two questions this trace always raises\n")
    doc.append(
        "**Why are there three near-identical `add` calls in section `01`?** "
        "Compare any two of them: they differ only in `id` and "
        "`progressToken`. Everything else, including `Content-Length`, is "
        "identical, and both return `5.0`. Those two fields are correlation "
        "handles, not state: `id` is how JSON-RPC matches a response to its "
        "request, and `progressToken` is the label a server would attach "
        "progress notifications to. Repeating the call is the point. Same input, "
        "same output, nothing accumulating on the server. Compare with section "
        "`02`, where three identical `increment` calls return 1, 2 and 3.\n"
    )
    doc.append(
        "**Why does `tools/list` come *after* the first `tools/call`?** The "
        "order is discover, call, list, call, call, which looks backwards. Note "
        "first that `04_client_demo.py` never asks for a tool list at all: it "
        "only calls `add` three times. That request is emitted by the fastmcp "
        "client itself, from inside the code that parses a call result:\n"
    )
    doc.append(
        "```python\n"
        "# fastmcp/client/mixins/tools.py\n"
        "# Ensure the schema cache is populated for type validation.\n"
        "if name not in tool_output_schemas:\n"
        "    await list_tools_fn()\n"
        "```\n"
    )
    doc.append(
        "So the sequence is: send the first call, get the response back, and "
        "*while deserializing it* discover that the tool's output schema is not "
        "cached yet. The client fetches the schema then, uses it to coerce "
        "`structuredContent` into a typed value, and keeps it for the rest of "
        "the connection. Calls two and three skip the fetch.\n"
    )
    doc.append(
        "Two things worth taking from that. It is a **client library detail, "
        "not a protocol rule**: another SDK is free to list tools up front. And "
        "the cache lives in the **client**, so the server still stores nothing "
        "between requests. Client-side caching and a stateless server are not "
        "in conflict.\n"
    )

    doc.append("## Load balancing: why the session is *not* in a header\n")
    doc.append(
        "Reading the trace, a lot is exposed in headers for routing, but the "
        "session id is not. In section `03` it appears only inside the JSON "
        "body, as a normal `session_id` entry in `arguments`. That is "
        "deliberate.\n"
    )
    doc.append(
        "Routing on a session is exactly the sticky-session behaviour the "
        "2026-07-28 spec set out to remove. Under the old model the session "
        "lived in one process's memory, so the balancer *had* to pin a client "
        "to the replica that held it. Under the new model the session id is "
        "just a lookup key into a shared store, so any replica can serve any "
        "request and there is nothing to route on. Plain round-robin is the "
        "point.\n"
    )
    doc.append(
        "**If you do need it in a header, there is a supported opt-in.** "
        "Annotate the argument in the tool's input schema with `x-mcp-header` "
        "and the client mirrors it into an `Mcp-Param-<token>` header:\n"
    )
    doc.append(
        "```python\n"
        "from typing import Annotated\n"
        "from pydantic import Field\n"
        "\n"
        "@mcp.tool\n"
        "async def increment(\n"
        "    session_id: Annotated[\n"
        "        SessionId,\n"
        '        Field(json_schema_extra={"x-mcp-header": "session"}),\n'
        "    ]\n"
        ") -> int:\n"
        "    ...\n"
        "```\n"
    )
    doc.append("which puts this on the wire, alongside the value in the body:\n")
    doc.append(
        "```http\n"
        "POST /mcp HTTP/1.1\n"
        "mcp-method: tools/call\n"
        "mcp-name: increment\n"
        "Mcp-Param-session: 05fb378c-3e69-4e28-8b7e-c8e68d50d4a4\n"
        "```\n"
    )
    doc.append(
        "The server validates that the header agrees with the body, so the two "
        "cannot drift apart.\n"
    )

    doc.append("### But something still has to know about the state\n")
    doc.append(
        "Correct, and this is the honest version of the story. Statelessness "
        "does not delete the state, it **moves** it. What actually changed is "
        "narrower than the marketing suggests:\n"
    )
    doc.append(
        "| | Sticky sessions (old) | Shared store (new) |\n"
        "|---|---|---|\n"
        "| Where state lives | one replica's memory | a store both replicas read |\n"
        "| Routing is | a **correctness** requirement | irrelevant to correctness |\n"
        "| Route to the wrong replica | session lost, request broken | works fine |\n"
        "| Replica restarts | sessions on it are gone | unaffected |\n"
        "| Cost | LB config coupled to app state | you now operate a store |\n"
    )
    doc.append(
        "So the load balancer does not need to be state-aware, but your "
        "*architecture* still does. You traded routing complexity for storage "
        "complexity: an extra network hop on stateful calls, a new dependency "
        "and failure domain, and a retention policy to choose. FastMCP writes "
        "never set a TTL themselves, so expiry is entirely the store's "
        "(`redis`, `valkey`, `postgresql`, `dynamodb`, `mongodb`, `memcached` "
        "and others are available through the `AsyncKeyValue` interface).\n"
    )
    doc.append(
        "**One caveat worth knowing before you fan out.** FastMCP's own "
        "docstring for `Session` says: *\"Concurrent writes to one session race "
        "on the read-modify-write; session state is small and typically driven "
        "serially by one agent, so this is acceptable.\"* That is an assumption, "
        "not a guarantee. Two requests for the same session landing on two "
        "replicas at the same moment can lose an update, because `get` then "
        "`set` is not atomic. Fine for one agent working through a "
        "conversation, not fine for concurrent fan-out over a shared session. "
        "If you need that, use a store with atomic operations or keep writes "
        "serialised per session.\n"
    )
    doc.append(
        "This is also where session-aware routing earns its place. Hashing on "
        "`Mcp-Param-session` gives you cache locality and narrows that race "
        "window, but as an **optimisation**: if the routing misses, the request "
        "is still correct, just a store round-trip slower. That is the real "
        "difference from sticky sessions, where a routing miss was a bug.\n"
    )

    doc.append("### One endpoint, two protocols at once\n")
    doc.append(
        "A modern server accepts both eras, so at any moment a single endpoint "
        "may be serving a mix, and every gateway, load balancer, WAF or tracing "
        "tool in front of it sees that mix too. Measured by pointing a modern "
        "client (`mcp 2.0.0`) and a legacy one (`mcp 1.29.0`) at the same "
        "server and capturing on one port:\n"
    )
    doc.append(
        "```\n"
        "3 requests   mcp-protocol-version: 2026-07-28   + mcp-method + mcp-name\n"
        "3 requests   mcp-protocol-version: 2025-11-25   (no routing headers)\n"
        "```\n"
    )
    doc.append(
        "**The routing headers are modern-only.** `mcp-method` and `mcp-name` "
        "appeared on the modern client's requests and on none of the legacy "
        "ones. Any rule that routes on them silently covers only part of your "
        "traffic; for legacy clients an intermediary still has to parse the "
        "JSON-RPC body to know which method is being called.\n"
    )
    doc.append(
        "Worse for classification: the legacy client's very first request, the "
        "`initialize`, carried **no** `mcp-protocol-version` header at all, "
        "because the version is what that handshake is negotiating. So the one "
        "request that opens a legacy conversation is also the one an "
        "intermediary cannot classify from headers alone.\n"
    )
    doc.append(
        "Practical guidance for anything sitting in front of MCP servers: treat "
        "header-based routing as an optimisation with a body-parsing or "
        "default-route fallback, never as complete coverage. And expect the mix "
        "to shift under you. The era a client speaks follows its `mcp` library "
        "version, so a dependency bump on the client side changes what your "
        "infrastructure sees, with no server change at all.\n"
    )

    doc.append(
        "\n> Captured on loopback with a full snaplen "
        "(`tcpdump -i lo0 -s 0`). A truncated snaplen silently cuts off the "
        "larger `tools/list` responses.\n"
    )

    current_stream = None
    step = 0
    for req in reqs:
        if len(req) < 7:
            continue
        frame, stream, port, method, uri, hdrs, body_hex = req[:7]

        if stream != current_stream:
            current_stream = stream
            step = 0
            label, note = SERVERS.get(port, (f"port {port}", ""))
            doc.append(f"\n---\n\n## {label}\n")
            doc.append(f"`127.0.0.1:{port}` ({note})\n")

        step += 1
        req_body = decode_body(body_hex)
        title = summarise(req_body) or f"{method} {uri}"
        doc.append(f"\n### {step}. {title}\n")

        doc.append("**Request**\n")
        block = [f"{method} {uri} HTTP/1.1"] + clean_headers(hdrs)
        pj = pretty_json(req_body)
        if pj:
            block += ["", pj]
        doc.append("```http\n" + "\n".join(block) + "\n```\n")

        resp = by_request.get(frame)
        if resp:
            code = resp[2]
            rhdrs = clean_headers(resp[4]) if len(resp) > 4 else []
            rbody = decode_body(resp[5]) if len(resp) > 5 else ""
            doc.append("**Response**\n")
            rblock = [f"HTTP/1.1 {code}"] + rhdrs
            rj = pretty_json(rbody)
            if rj:
                rblock += ["", rj]
            doc.append("```http\n" + "\n".join(rblock) + "\n```\n")
        else:
            doc.append("_No response body captured (notification or 202)._\n")

    Path(out_path).write_text("\n".join(doc))
    print(f"wrote {out_path}  ({len(reqs)} exchanges)")


if __name__ == "__main__":
    pcap = sys.argv[1] if len(sys.argv) > 1 else "mcp_stateless.pcap"
    out = sys.argv[2] if len(sys.argv) > 2 else "PROTOCOL_TRACE.md"
    build(pcap, out)
