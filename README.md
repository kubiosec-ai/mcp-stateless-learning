# Stateless vs Stateful MCP — a hands-on mini-training

Simple, runnable Python examples for teaching the difference between
**stateless** and **stateful** MCP servers, built on **FastMCP 4.0.0b1**.

Everything here is verified to run on that exact version.

## Setup

```bash
pip install -r requirements.txt      # fastmcp==4.0.0b1
```

## Running it

Each server is a **standalone HTTP program** — start them yourself and
leave them running. They're independent and reusable: point *any* MCP
client at these URLs (Claude, an IDE, another SDK, curl), not just the
demo client here.

Start the three servers, each in its own terminal:

```bash
python 01_stateless_server.py        # -> http://127.0.0.1:8001/mcp
python 02_stateful_global_server.py  # -> http://127.0.0.1:8002/mcp
python 03_stateful_session_server.py # -> http://127.0.0.1:8003/mcp
```

Then, in another terminal, run the demo client that exercises all three:

```bash
python 04_client_demo.py
```

Re-run `02` behaviour to see the point: kill and restart
`02_stateful_global_server.py` and its counter resets to 0 (process-local
state), while `01` is unaffected because it has none.

### Two transports

Every server runs either way — HTTP by default, stdio with `--stdio`:

```bash
python 01_stateless_server.py            # Streamable HTTP on a port
python 01_stateless_server.py --stdio    # stdio (Inspector, Claude Desktop, IDEs)
```

## Using the MCP Inspector

`npx @modelcontextprotocol/inspector` only opens the UI — it does **not**
connect to anything by itself. That's why "nothing happens". You have to
point it at a server. Two ways:

**A. Against a running HTTP server** (servers already started as above):

1. Open the printed URL, e.g. `http://localhost:6274?MCP_INSPECTOR_API_TOKEN=...`
   — keep the token in the URL, or paste it into the Inspector's token field.
2. Set **Transport Type** to `Streamable HTTP`.
3. Set **URL** to `http://127.0.0.1:8001/mcp` (note the `/mcp` path — leaving
   it off is the most common mistake).
4. Click **Connect**, then open the **Tools** tab and hit **List Tools**.

You should see `add`, `multiply`, `celsius_to_fahrenheit`.

**B. Let Inspector launch the server itself over stdio** — no server needs to
be running first:

```bash
npx @modelcontextprotocol/inspector python 03_stateful_session_server.py --stdio
```

Set Transport to `STDIO` if it isn't already, then Connect.

### Troubleshooting: Inspector stuck on "Connecting..." in Firefox

**Use Chrome for the Inspector.** Firefox can sit on "Connecting..." forever
against a perfectly healthy server.

It helps to know how Inspector 2.0.0 is wired. The browser does **not** talk
to your MCP server directly — it drives a Node backend:

```
browser  --POST /api/mcp/connect-->  Inspector backend  --HTTP-->  your server :8001
         <--SSE  /api/mcp/events---
```

The connection to your server is made by the backend. The green
"Connected" state in the UI, though, arrives over a **separate SSE stream**
at `GET /api/mcp/events?sessionId=...`, read with `fetch` (not native
`EventSource`, because it has to attach the auth-token header).

So if that stream doesn't surface incrementally in your browser, the UI never
leaves "Connecting..." — **even when the MCP connection already succeeded**.
The spinner is reporting the browser↔backend channel, not your server.
Nothing you change in the Python will affect it.

Two ways past it:

```bash
# 1. Use Chrome for the UI.

# 2. Skip the browser entirely — the CLI exercises the same backend path:
npx @modelcontextprotocol/inspector@2.0.0 --cli http://127.0.0.1:8001/mcp --method tools/list
npx @modelcontextprotocol/inspector@2.0.0 --cli http://127.0.0.1:8001/mcp \
    --method tools/call --tool-name add --tool-arg a=2 --tool-arg b=3
```

`--cli` is the honest health check for a server, and it's what to use in the
training when you just want to prove a server works.

Also note this rules your server out as the suspect: CORS is irrelevant here
(the browser never calls port 8001), and stateless mode is fine
(`stateless_http=True` connects and runs `tools/call` correctly).

### Separately: proxy environment variables break all HTTP transports

This is a *different* Inspector 2.0.0 bug, worth knowing because corporate
laptops often ship with proxy variables set. It is not the Firefox issue
above. If stdio connects fine but *every* HTTP server fails, check the shell
you launched the Inspector from:

```bash
env | grep -i proxy
```

If `HTTP_PROXY`, `HTTPS_PROXY`, `http_proxy` or `https_proxy` is set,
that's the cause. Launch the Inspector with them stripped:

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy \
  npx @modelcontextprotocol/inspector
```

#### Why

Inspector 2.0.0 wraps its fetch like this (`clients/web/build/index.js`):

```js
function withProxyDispatcher(baseFetch) {
  if (readProxyEnv() === undefined) return baseFetch;   // no proxy -> plain fetch
  const proxiedFetch = async (input, init) => {
    const dispatcher = await getDispatcher();           // undici EnvHttpProxyAgent
    return baseFetch(input, { ...init, dispatcher });
  };
  return proxiedFetch;
}
```

Two problems. It applies the proxy dispatcher to **every** request,
including `127.0.0.1`, and the dispatcher comes from its own bundled
`undici@8` while the request runs through Node's **built-in** fetch, whose
internal undici is a different version. Node rejects the foreign dispatcher:

```
{"error":{"code":"unreachable","message":"fetch failed",
          "cause":"invalid onRequestStart method"}}
```

stdio is unaffected because it spawns a child process and never calls fetch —
exactly the "stdio works, HTTP doesn't" symptom.

Reproduced against FastMCP 4.0.0b1 on this exact setup:

```
# with HTTPS_PROXY set
$ npx @modelcontextprotocol/inspector@2.0.0 --cli http://127.0.0.1:8001/mcp --method tools/list
{"error":{"code":"unreachable","message":"fetch failed","cause":"invalid onRequestStart method"}}

# same command, proxy vars stripped
$ env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy \
    npx @modelcontextprotocol/inspector@2.0.0 --cli http://127.0.0.1:8001/mcp --method tools/list
{ "tools": [ { "name": "add", ... } ] }
```

The `--cli` flag above is also the fastest way to test a server without the
UI at all.

#### Changing the bind address does NOT help

A natural guess is that `127.0.0.1` is the problem and binding to a real LAN
address would dodge the proxy. It doesn't — the dispatcher is rejected before
any routing decision happens, so the host is irrelevant. Nor does `NO_PROXY`
help: the wrapper only checks *whether* a proxy variable is set, never
whether the target is exempt. Full matrix, all measured:

| Server address | Proxy env | Result |
|---|---|---|
| `127.0.0.1:8001` | set | fails — `invalid onRequestStart method` |
| `127.0.0.1:8001` | unset | works |
| LAN IP (`0.0.0.0` bind) | set | fails — identical error |
| LAN IP | set + `NO_PROXY='*'` | still fails |
| LAN IP | unset | works |

Unsetting the proxy variables is the only fix. Keep binding to `127.0.0.1`.

#### Note on stateless mode and the Inspector

All three servers connect fine once the proxy issue is out of the way,
**including `01` with `stateless_http=True`** — verified with both
`tools/list` and `tools/call add {a:2,b:3}` → `5.0`.

One real difference worth showing students: in stateless mode there is no
session and therefore no server→client SSE stream, so `GET /mcp` answers
`405 Method Not Allowed` (`allow: POST, DELETE`), while a session-enabled
server holds that stream open. It doesn't block the Inspector, but it's the
concrete reason the 2026-07-28 spec had to redesign server-initiated flows
(logs, progress, elicitation) instead of just deleting sessions. If you want
to show both sides live:

```bash
python 01_stateless_server.py                  # stateless: GET /mcp -> 405
python 01_stateless_server.py --with-sessions  # same tools, sessions enabled
```

### Good things to demo in the Inspector

- On **8001** (stateless): call `add` with the same arguments repeatedly —
  the answer never changes and there's no session to inspect.
- On **8003** (per-session): call `create_session` first, copy the returned
  id, then call `increment` with that `session_id` several times and watch
  1, 2, 3… Call `create_session` again for a second id and see it start
  back at 1. This makes the explicit-handle pattern visible.

## The idea in one line

| | Depends on… | Memory between calls? | Scaling |
|---|---|---|---|
| **Stateless** | only the current arguments | no | trivial: any request → any replica |
| **Stateful** | current args **+** past calls | yes | needs a shared store or session affinity |

Mental model: a **stateless** tool is a pure function `f(input) -> output`;
a **stateful** server *remembers* things, so `increment()` returns 1, 2, 3…

## The files

- **`01_stateless_server.py`** — pure calculator tools. `add(2,3)` is
  always `5`. Runs with `stateless_http=True`: the HTTP layer keeps no
  per-client session.
- **`02_stateful_global_server.py`** — the simplest possible stateful
  server: a module-level counter and notes list. Great first lesson, but
  the state is **global** (shared by all clients) and **process-local**
  (lost on restart, not shared across replicas).
- **`03_stateful_session_server.py`** — the FastMCP 4 way. **Per-session**
  state via the "explicit handle" pattern, so state is isolated per client
  *and* the transport stays stateless. This is the one that matches how the
  modern MCP spec wants you to do it.
- **`05_compat_check.py`** — reports which MCP SDK you have and whether it can
  reach each server. Run it from a modern and a legacy environment to see
  backward compatibility for yourself; see
  [COMPATIBILITY.md](COMPATIBILITY.md).
- **`04_client_demo.py`** — one example driver that connects to the three
  servers over HTTP. It's just *one* consumer; the servers stand on their
  own and can be reused by anything that speaks MCP.

### How example 03 works (the important pattern)

1. `mcp.add_provider(SessionProvider())` adds two tools: `create_session()`
   and `end_session(session_id)`.
2. The client calls `create_session()` and gets an unguessable id (a *handle*).
3. It passes that id as `session_id=...` on later calls. A tool argument
   typed `session_id: SessionId` receives it.
4. Inside the tool, `await get_session(session_id)` returns a `Session` with
   async `.get()`, `.set()`, `.delete()`, `.clear()`.

Because the id travels as a normal argument, any server replica can serve
any request — no sticky sessions. Swap the default in-memory store for a
shared one (`FastMCP(name, session_state_store=<AsyncKeyValue>)`, e.g. Redis)
and every replica sees the same state.

Sample output from `04_client_demo.py`:

```
session A: ac010842-...     session B: a042fb9d-...
A.increment() -> 1
A.increment() -> 2
A.increment() -> 3
B.increment() -> 1     # B is independent
A.increment() -> 4     # A kept its own count
```

## Why this matters right now (ecosystem status, Aug 2026)

The industry moved hard toward **stateless MCP** in mid-2026, which is why
this distinction is worth teaching:

- **MCP spec `2026-07-28`** restructured the protocol from session-based to
  **stateless at its core**: it removed the `initialize`/`initialized`
  handshake and the `Mcp-Session-Id` header (SEP-2575, SEP-2567). State that
  used to hide in the transport is now an **explicit handle** the client
  passes back as a tool argument — exactly the pattern in example 03. Remote
  servers can now sit behind plain round-robin load balancers.
- **FastMCP 4** (beta, used here) implements that model: sessionless protocol
  by default, with `UserSession` (per authenticated user) and the auth-free
  `SessionProvider` + `SessionId` handles shown in example 03 for keeping
  state without sticky sessions.
- **Anthropic / Claude** announced bringing the `2026-07-28` stateless spec to
  Claude; the Claude Agent SDK consumes MCP servers.
- **OpenAI Agents SDK** connects to MCP over stdio, Streamable HTTP, and
  (deprecated) SSE, plus hosted MCP tools via the Responses API. Notably,
  OpenAI's own Responses API leans **stateful** server-side — an interesting
  contrast to MCP going stateless.
- **Microsoft Agent Framework 1.0** shipped GA (April 2026, .NET + Python) and
  supports MCP tools; BUILD 2026 added the Agent Harness, hosted agents, and
  CodeAct.

Takeaway for students: prefer **stateless** tools by default (they scale and
deploy trivially); when you genuinely need memory, make the state **explicit**
(a handle you pass around) and back it with a **shared store** rather than
relying on which process happened to answer the call.

## Sources

- [MCP 2026-07-28 release candidate](https://blog.modelcontextprotocol.io/posts/2026-07-28-release-candidate/)
- [FastMCP 4 — what's new](https://gofastmcp.com/getting-started/whats-new)
- [Bringing MCP 2026-07-28 to Claude](https://claude.com/blog/bringing-mcp-2026-07-28-to-claude)
- [OpenAI Agents SDK — MCP](https://openai.github.io/openai-agents-python/mcp/)
- [Two Opposite Paths: MCP Goes Stateless, OpenAI Goes Stateful](https://yage.ai/share/mcp-vs-openai-state-en-20260703.html)
- [Microsoft ships Agent Framework 1.0 (.NET & Python)](https://visualstudiomagazine.com/articles/2026/04/06/microsoft-ships-production-ready-agent-framework-1-0-for-net-and-python.aspx)
