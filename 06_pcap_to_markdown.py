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
    "mcp-session-id",
    "mcp-target",
    "content-length",
)


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
        if h and h.split(":")[0].strip().lower() in KEEP_HEADERS:
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
        "**4. Routing headers mirror the body.** `mcp-protocol-version` and "
        "`mcp-method` appear as HTTP headers, so a gateway or load balancer can "
        "route on them without parsing the JSON-RPC payload.\n"
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
