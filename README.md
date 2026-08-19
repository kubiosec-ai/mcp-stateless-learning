# Stateless vs Stateful MCP: a hands-on mini-training

Small, runnable Python examples that make the difference between **stateless**
and **stateful** MCP servers concrete. Built on **FastMCP 4.0.0b1**.

## The idea in one line

A **stateless** tool is a pure function: the answer depends only on the
arguments you pass. A **stateful** server *remembers* things between calls, so
the answer also depends on what happened before.

| | Depends on... | Memory between calls? | Scaling |
|---|---|---|---|
| **Stateless** | only the current arguments | no | trivial: any request, any replica |
| **Stateful** | current args **plus** past calls | yes | needs a shared store or session affinity |

## Setup

```bash
pip install -r requirements.txt      # fastmcp==4.0.0b1
```

## The three demo servers

Each server is a standalone program. They are deliberately tiny, because the
point is the behaviour, not the tools.

### `01_stateless_server.py`: no memory at all

Port **8001**. Three pure tools: `add`, `multiply`, `celsius_to_fahrenheit`.

`add(2, 3)` returns `5` every single time. There is nothing to remember, so any
replica can answer any request. Runs with `stateless_http=True`, meaning the
HTTP layer does not even keep a session per client.

**Teaches:** the baseline. This is what you should write by default.

### `02_stateful_global_server.py`: the server remembers

Port **8002**. Tools: `increment`, `get_counter`, `add_note`, `list_notes`.

Call `increment()` three times and you get 1, 2, 3. The server is holding the
value between calls, in a plain module-level Python variable.

**Teaches:** what "stateful" means, in the simplest possible form, and its two
problems. The state is *global* (every client shares one counter) and
*process-local* (restart the server and it resets to 0, and a second replica
would have its own separate copy). Kill and restart it mid-demo to show this.

### `03_stateful_session_server.py`: per-session memory, done properly

Port **8003**. Tools: `increment`, `remember`, `recall`, plus `create_session`
and `end_session`.

This fixes both of `02`'s problems while keeping the transport stateless. Each
client gets its own isolated memory, and the state lives in a store you can
point at Redis instead of one process's RAM.

**Teaches:** the pattern the modern MCP spec actually wants. See below.

## Running it

Start the servers, each in its own terminal:

```bash
python 01_stateless_server.py        # -> http://127.0.0.1:8001/mcp
python 02_stateful_global_server.py  # -> http://127.0.0.1:8002/mcp
python 03_stateful_session_server.py # -> http://127.0.0.1:8003/mcp
```

Then run the client that exercises all three:

```bash
python 04_client_demo.py
```

The output is the whole lesson:

```
=== 01 STATELESS ===
  add(2, 3) -> 5.0
  add(2, 3) -> 5.0          # never changes

=== 02 STATEFUL (global) ===
  increment() -> 1
  increment() -> 2
  increment() -> 3          # the server remembers

=== 03 STATEFUL (per-session) ===
  A.increment() -> 1
  A.increment() -> 2
  A.increment() -> 3
  B.increment() -> 1        # a different session starts fresh
  A.increment() -> 4        # A kept its own count
```

### Two transports

Every server runs either way: HTTP by default, stdio with `--stdio`.

```bash
python 01_stateless_server.py            # Streamable HTTP on a port
python 01_stateless_server.py --stdio    # stdio (Claude Desktop, IDEs, Inspector)
```

## How example 03 works: the explicit-handle pattern

This is the most important idea in the training.

1. `mcp.add_provider(SessionProvider())` registers `create_session()` and
   `end_session(session_id)`.
2. The client calls `create_session()` and gets back an unguessable id, a
   *handle*.
3. It passes that id as `session_id=...` on later calls. A tool argument typed
   `session_id: SessionId` receives it.
4. Inside the tool, `await get_session(session_id)` returns a `Session` with
   async `.get()`, `.set()`, `.delete()`, `.clear()`.

Because the id travels as an ordinary tool argument, **any replica can serve
any request**, with no sticky sessions. Swap the default in-memory store for a
shared one (`FastMCP(name, session_state_store=<AsyncKeyValue>)`) and every
replica sees the same state.

That is the trick: the state is real, but it is *explicit* and visible instead
of hidden inside the transport.

## Inspecting the servers

```bash
npx @modelcontextprotocol/inspector
```

Set Transport to `Streamable HTTP` and URL to `http://127.0.0.1:8001/mcp`
(do not forget the `/mcp` path), then Connect, Tools, List Tools.

> **Use Chrome.** The Inspector UI can hang on "Connecting..." in Firefox.

To check a server without the UI at all:

```bash
npx @modelcontextprotocol/inspector --cli http://127.0.0.1:8001/mcp --method tools/list
```

Worth demoing on **8003**: call `create_session`, paste the returned id into
`increment`, and watch it climb 1, 2, 3. Mint a second session and it starts
back at 1.

## Seeing the protocol on the wire

`PROTOCOL_TRACE.md` is a readable, packet-level walkthrough of what
`04_client_demo.py` actually sends: every HTTP request and response with the
JSON-RPC bodies pretty-printed. It is the fastest way to understand what the
2026-07-28 protocol looks like, because you can see that there is no
handshake, no session header, and that `session_id` is just a normal tool
argument.

It is generated from the included capture:

```bash
python 06_pcap_to_markdown.py mcp_stateless.pcap PROTOCOL_TRACE.md
```

To capture your own (needs `tcpdump` and `tshark`):

```bash
sudo tcpdump -i lo0 -s 0 -w mcp_stateless.pcap \
    'tcp port 8001 or tcp port 8002 or tcp port 8003'

# in another terminal, with the servers running
python 04_client_demo.py
```

Use `-s 0`. A truncated snaplen silently cuts off the larger `tools/list`
responses.

## SDK compatibility

```bash
python 05_compat_check.py
```

Reports which MCP SDK you have, which protocol era it belongs to, and whether
it can reach each server.

Short version: the ecosystem is mid-migration and moving quickly. As of
2026-08-18 the OpenAI Agents SDK has crossed to the modern SDK (`0.21.1`
pins `mcp<3`), while Microsoft's `agent-framework-core` is still on `mcp<2`.
Both talk to these servers fine, which is the point. See
[COMPATIBILITY.md](COMPATIBILITY.md) for the measured matrix, and re-run the
checker rather than trusting a table that ages this fast.

## The files

- `01_stateless_server.py`: stateless server (port 8001)
- `02_stateful_global_server.py`: stateful via a global variable (port 8002)
- `03_stateful_session_server.py`: per-session state, explicit handles (port 8003)
- `04_client_demo.py`: client that exercises all three over HTTP
- `05_compat_check.py`: SDK and protocol-era compatibility checker
- `06_pcap_to_markdown.py`: turns a packet capture into `PROTOCOL_TRACE.md`
- `07_gateway.py`: an MCP gateway federating the three servers, with policy middleware
- `PROTOCOL_TRACE.md`: the protocol walkthrough, generated from `mcp_stateless.pcap`
- `COMPATIBILITY.md`: SDK versions, backward compatibility, deprecation timeline
- `LOAD_BALANCER.md`: running MCP behind a load balancer, persistence options assessed

## Running it behind a load balancer

`LOAD_BALANCER.md` covers what changes when you scale past one replica: which
server settings decide whether you need session affinity at all, why L4 and
source-IP stickiness do not work for MCP, what cookie insertion does and does
not buy you, and how to route on a session handle without parsing JSON. It
also documents the silent lost-update race that appears the moment two
replicas share a session store.

## Why this matters (August 2026)

The MCP spec `2026-07-28` restructured the protocol from session-based to
**stateless at its core**, removing the `initialize` handshake and the
`Mcp-Session-Id` header. State did not disappear. It became an explicit handle
the client passes back, exactly as in example `03`. Remote servers can now sit
behind plain round-robin load balancers.

The takeaway for students: prefer **stateless** tools by default. When you
genuinely need memory, make the state **explicit** and back it with a **shared
store** rather than relying on which process happened to answer the call.

## Sources

- [The 2026-07-28 Specification](https://blog.modelcontextprotocol.io/posts/2026-07-28/)
- [FastMCP 4 what's new](https://gofastmcp.com/getting-started/whats-new)
- [Bringing MCP 2026-07-28 to Claude](https://claude.com/blog/bringing-mcp-2026-07-28-to-claude)
