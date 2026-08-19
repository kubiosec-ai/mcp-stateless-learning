# Load balancing MCP servers

Everything here was measured against the servers in this repo, on FastMCP
4.0.0b1, with real packet captures. Where a claim is testable, the evidence is
shown. Reproduction steps are in the appendix.

The short version: the stateless spec removes the *protocol's* need for
session affinity, and that is a real win. It does not remove your
*application's* need for it, and the mechanisms most load balancers offer to
provide affinity all have gaps that are specific to MCP.

## 1. The claim, and where it stops being true

The pitch for the 2026-07-28 spec is that MCP servers sit behind plain
round-robin load balancers with no sticky sessions. The `initialize` handshake
and the `Mcp-Session-Id` header are gone, so there is no transport session
pinning a client to one replica.

That claim is true, and narrower than it sounds. Three things survive it:

1. **Legacy clients still exist**, and a legacy conversation can still carry
   `Mcp-Session-Id`.
2. **Application state did not disappear**, it moved to a shared store. That
   works for data and not for live resources.
3. **Concurrency got no easier.** Removing affinity actively makes one class
   of bug more likely.

## 2. What actually decides whether you need persistence

Not the client. Measured by checking which combinations cause a session id to
be issued at all:

| Negotiated era | Server `stateless_http` | `Mcp-Session-Id` | Affinity required |
|---|---|---|---|
| modern (2026-07-28) | `True` | no | no |
| modern | `False` | no | no |
| legacy (2025-11-25) | `True` | **no** | **no** |
| legacy | `False` | yes | **yes** |

Evidence: a legacy client against a `stateless_http=True` server produced
**0** occurrences of `Mcp-Session-Id`. The same client against a
session-keeping server produced **9**. A modern client produced **0** against
every server regardless of setting, because the modern protocol has no session
concept to use.

**First conclusion.** The selector is your own server configuration. Run
`stateless_http=True` across the fleet and no conversation in either era gets
a transport session. That is a deployment decision you can audit, not a
traffic-inspection problem you have to solve at runtime.

## 3. Two kinds of state, and only one of them is easy

Turning off transport sessions does not mean your application is stateless.
Ask what is behind the handle.

### Data-shaped state

Serialisable values: a counter, a list of facts, a cart, a cursor. Put it in a
store both replicas can read and any replica can serve any request.

Note that the explicit-handle pattern alone is not enough. Two replicas of
`03_stateful_session_server.py` with the default in-memory store:

```
replica A: created session, increment -> 1
replica B: FAILED -> ToolError: Invalid or unknown session.
```

The handle was valid. The store was process-local, so the second replica had
never heard of it. Configure a shared `session_state_store` and this class of
failure goes away.

### Resource-shaped state

A live thing that cannot be serialised: an interpreter or REPL kernel holding
variables and imports, a code-execution container, a headless browser context,
an SSH session, an open transaction.

No store fixes this. The request has to reach the machine that holds the
resource. Affinity is back, and the only question is where you implement it.

## 4. The persistence mechanisms, assessed

This is the part that matters if your load balancer cannot parse JSON.

### 4.1 No persistence (round robin)

**Works when:** every server runs `stateless_http=True` and all state is
data-shaped in a shared store.

**Recommended default.** If you can get here, stop. Nothing below is free.

### 4.2 L4 / connection-based affinity

**Does not work.** Measured connections per conversation:

| Client | HTTP requests | TCP connections |
|---|---|---|
| modern (`mcp 2.0.0`) | 3 | **1** |
| legacy (`mcp 1.29.0`) | 4 | **4** |
| modern falling back to legacy server | 7 | 6 |

The legacy client opens a **new TCP connection per request**. Connection-level
affinity therefore pins nothing: every request is balanced independently. Even
the modern client, which does reuse a keep-alive connection, only reuses it for
the lifetime of one client object.

Do not rely on L4 stickiness for MCP.

### 4.3 Source IP affinity

**Usually ineffective, occasionally harmful.** Agent traffic concentrates:
egress NAT, a Kubernetes node, an API gateway, or a single automation host all
collapse many logical clients to one source address. You get hot-spotting
rather than distribution, and no isolation between the agents sharing that IP.

It also fails in the other direction, since a client that changes egress
address mid-conversation loses its pin.

### 4.4 Cookie insertion (LB-generated)

**Works, within one client lifetime.** This is the good news for load
balancers that cannot parse a JSON body.

Measured by injecting `Set-Cookie: LBAFFINITY=node-a` from a middleware in
front of the server and logging what came back:

```
REQ POST /mcp cookie=None                        # first request
REQ POST /mcp cookie=b'LBAFFINITY=node-a'        # and every one after
REQ POST /mcp cookie=b'LBAFFINITY=node-a'
```

Both eras honour it. The legacy `openai-agents` client echoed the cookie back
just as the modern `fastmcp` client did, because both are built on `httpx`,
which keeps a cookie jar per client instance.

**The catch, also measured.** The jar dies with the client object:

```
REQ POST /mcp cookie=None                        # client #1, first request
REQ POST /mcp cookie=b'LBAFFINITY=node-a'
REQ POST /mcp cookie=b'LBAFFINITY=node-a'
--- new Client instance (reconnect) ---
REQ POST /mcp cookie=None                        # affinity lost
REQ POST /mcp cookie=b'LBAFFINITY=node-a'        # re-pinned, possibly elsewhere
```

So cookie insertion gives you **conversation affinity, not resource
affinity**. It holds a client to one replica for as long as that client object
lives. It will not bring a reconnecting agent back to the container it was
using five minutes ago.

That distinction decides whether cookies are enough:

- Session state is data in a shared store, and you want cookie affinity only
  as an optimisation for cache locality or to narrow the write-race window:
  **cookies are fine.**
- The handle refers to a live resource that must outlive the client object:
  **cookies are not enough.** The reconnect lands anywhere.

Two operational notes. Some MCP clients may disable cookie persistence or be
configured with a fresh transport per call, so verify against the clients you
actually serve rather than assuming. And a cookie set by the balancer is
visible to the client; use `HttpOnly` and treat the node identifier as
information you are willing to disclose.

### 4.5 Routing on MCP headers

The modern protocol mirrors routing information into headers so an
intermediary does not have to parse JSON-RPC:

```http
mcp-protocol-version: 2026-07-28
mcp-method: tools/call
mcp-name: add
```

**The gap: these are modern-only.** In a capture of both eras hitting one
endpoint, `mcp-method` and `mcp-name` appeared on the modern client's requests
and on **none** of the legacy ones. A rule keyed on them silently covers part
of your traffic and misses the rest, without erroring.

Worse for classification, the legacy `initialize` carries **no**
`mcp-protocol-version` header at all, because that request is what negotiates
the version. The one request that opens a legacy conversation is the one you
cannot classify from headers.

Useful for observability and coarse routing. Not a basis for correctness.

### 4.6 Parsing the JSON body for `session_id`

In the explicit-handle pattern the session id travels inside `params.arguments`
of a `tools/call`. Routing on it requires the balancer to buffer and parse a
JSON-RPC body on every request.

Many load balancers cannot do this at all, and those that can pay for it:
full request buffering, CPU on every call, brittleness when the payload shape
changes, and no help at all for the requests that carry no handle.

**Avoid.** The next option exists precisely so you do not have to.

### 4.7 Promoting the handle to a header (`x-mcp-header`)

The supported way to make a handle routable. Annotate the argument in the
tool's input schema and the client mirrors it into an `Mcp-Param-<token>`
header:

```python
from typing import Annotated
from pydantic import Field
from fastmcp.server.sessions import SessionId

@mcp.tool
async def increment(
    session_id: Annotated[
        SessionId,
        Field(json_schema_extra={"x-mcp-header": "session"}),
    ]
) -> int:
    ...
```

Verified on the wire:

```http
POST /mcp HTTP/1.1
mcp-method: tools/call
mcp-name: increment
Mcp-Param-session: 05fb378c-3e69-4e28-8b7e-c8e68d50d4a4
```

The server validates that the header agrees with the body, so the two cannot
drift or be spoofed apart.

**This is the mechanism to use when you genuinely need handle-based routing.**
Any load balancer that can hash on a header can now do consistent hashing on a
session, with no JSON parsing. It is also durable in a way cookies are not:
the value comes from the request arguments, so a reconnecting client that
still holds the handle produces the same header and routes to the same node.

Requirements and limits: you must control the server to add the annotation,
only modern-era clients emit it, and requests without the argument (including
`create_session` itself) carry no header, so you still need a default route
for placement.

## 5. Persistence does not fix concurrency

Worth stating plainly, because it is the failure that actually hurts at scale
and no balancer setting prevents it.

Two replicas sharing a real store, increments fired concurrently and
alternated between them the way round robin would:

```
replica B accepts the handle -> 1            # shared store works
20 concurrent increments across 2 replicas -> counter 17  (LOST 3)
50 concurrent increments across 2 replicas -> counter 46  (LOST 4)
```

Every call returned success. No exception, no error response, nothing in a
log. `get` then `set` is a read-modify-write with a round trip in the middle,
so both replicas read the same value and the second write erases the first.

FastMCP documents the race in its `Session` docstring, calling it acceptable
because session state is "typically driven serially by one agent". That is the
assumption scale removes.

Fixes, best first:

1. **Commutative or idempotent writes**, so interleaving cannot lose anything.
   A design change, not an infrastructure one.
2. **Atomic store primitives**: a Redis `INCR` or compare-and-swap, instead of
   reading into Python and writing back.
3. **Serialise per session**, with a lock or by routing a session to one
   worker. This is the option that hands affinity back, and it is worth noting
   that per-session routing narrows the race window even when it is not
   strictly required.

## 6. Decision guide

Work down this list and stop at the first match.

**All state is data, in a shared store, all servers `stateless_http=True`.**
Plain round robin. No MCP awareness in the balancer. Ensure session writes are
commutative or use atomic store operations.

**As above, but you want cache locality or a narrower write-race window.**
Add cookie insertion, or consistent hashing on `Mcp-Param-*` if your clients
are modern. Both are optimisations: a miss costs a round trip, not
correctness.

**The handle refers to a live resource (container, kernel, browser context).**
Cookies are insufficient because they do not survive reconnects. Either route
on `Mcp-Param-*` with consistent hashing, or keep the servers stateless and
put an indirection layer behind them where any replica looks up which node
owns the resource and proxies. The first puts the mapping in infrastructure,
the second in your application. Also budget for the resource lifecycle: TTLs,
eviction, and orphan cleanup when a node dies.

**You serve legacy clients against session-keeping servers.** You need real
affinity for those flows. Cookie insertion is the most practical mechanism,
since legacy clients honour cookies but emit no routing headers. Better: set
`stateless_http=True` and remove the requirement.

## 7. The other option: an MCP gateway

Everything above tries to make a load balancer smart enough to handle MCP.
The alternative is to stop asking it to. A gateway is not a balancer with
extra features: it is an MCP **server** to the client and an MCP **client** to
the backends, so it acts on protocol semantics instead of guessing from
headers.

`07_gateway.py` is a working example. FastMCP ships the primitive
(`ProxyProvider`), so federating three backends behind one endpoint is a
handful of lines:

```python
gw = FastMCP("mcp-gateway", middleware=[GatewayPolicy(), SessionSerialiser()])
for namespace, url in BACKENDS.items():
    gw.add_provider(ProxyProvider(lambda u=url: Client(u), cache_ttl=300),
                    namespace=namespace)
```

Running it in front of the three demo servers:

```
gateway exposes:
  calc_add, calc_multiply, calc_celsius_to_fahrenheit,
  notes_increment, notes_get_counter, notes_add_note, notes_list_notes,
  sess_increment, sess_remember, sess_recall, sess_create_session, sess_end_session

[audit] calc_add ok 160ms (call #1)
  calc_add(2,3) -> 5.0
[audit] sess_increment ok 90ms (call #3)
  sess_increment x3 -> 3   (session state intact through the proxy)
```

### What it solves that a balancer cannot

**Era normalisation.** Terminate whatever the client speaks at the front and
speak one era to the backends. The mixed-era blind spot from section 4.5
disappears, and your backends stop caring which `mcp` version a client's
dependency resolver happened to pick.

**The handle-to-node mapping.** This is the indirection layer that
resource-shaped state needs (section 3), placed at the edge where you own it,
instead of reimplemented inside every server.

**Serialisation per session.** A lock keyed on `session_id` means the
interleaving that loses writes cannot happen, for every backend at once, with
no backend changes. See the honest status note in the code: the race was
reproduced at the store level, but not through the gateway, so treat the
middleware as designed rather than proven.

**A policy choke point that understands JSON-RPC.** Tool allow-lists,
per-tenant rate limits, argument validation, audit logging, and inspection of
tool *responses*. This is the thing a WAF cannot do because it cannot parse
the payload, and the reason a gateway is interesting for security review and
not only for scaling.

**Component-list caching.** `ProxyProvider` caches the backend's tool list
(300s by default), which removes the per-connection `tools/list` refetch
visible in `PROTOCOL_TRACE.md`.

### What it costs

You have concentrated state and trust into one component. It is now a single
point of failure and a high-value target that sees every tool call and every
argument, including anything sensitive passed to a tool. It adds a network
hop. Cache TTL means a tool added to a backend stays invisible for up to five
minutes. FastMCP marks proxied components `task_config.mode="forbidden"`, so
background tasks cannot run through a proxy and need a separate path. And the
in-process session lock only works for a single gateway instance; scale the
gateway out and you need a distributed lock or gateway-level affinity, which
is the same problem one layer up.

That last point is worth sitting with. A gateway does not eliminate the
affinity problem, it relocates it to a component you control and can reason
about. That is usually the right trade, and it is a trade rather than a fix.

## 8. Things that will surprise you

**The era a client speaks follows its `mcp` library version, not its
framework version.** `openai-agents 0.21.1` with `mcp 2.0.0` speaks
`2026-07-28`; the same `0.21.1` with `mcp 1.29.0` forced speaks `2025-11-25`.
A client-side `pip install -U` can change what your infrastructure sees with
no server change and no deploy on your side.

**Stickiness is a property of the negotiated pair.** A modern-capable client
talking to a legacy server probes with `server/discover`, gets refused, falls
back to `initialize`, and then carries `Mcp-Session-Id` on every subsequent
request. You cannot infer wire behaviour from the client alone.

**A refused probe still allocated state.** In that fallback capture, the
rejected `server/discover` came back carrying a session id of its own,
different from the one the conversation went on to use. An unauthenticated,
rejected request was enough to make a legacy server allocate session state.
Worth a thought when sizing, and when threat-modelling.

**Header-based rules fail open and silently.** They do not error on legacy
traffic, they simply do not match. The same blind spot applies to a WAF rule,
a DLP policy, or a tracing pipeline keyed on `mcp-method`, which makes this a
security concern and not only an operations one.

## 9. Appendix: reproducing the measurements

```bash
# session id issued or not, per server config
python 01_stateless_server.py          # stateless_http=True
python 02_stateful_global_server.py    # sessions enabled
# drive each with a legacy client and count Mcp-Session-Id on the wire

# connections per conversation
tshark -r capture.pcap -Y "tcp.flags.syn==1 && tcp.flags.ack==0" | wc -l
tshark -r capture.pcap -Y "http.request" | wc -l

# cookie handling: put an ASGI middleware in front of mcp.http_app() that
# injects Set-Cookie and logs the inbound Cookie header, then drive it with
# one client, a second client instance, and a legacy client

# lost updates: two replicas sharing a session_state_store, then
# asyncio.gather() concurrent increments on one session id alternating
# between replicas
```

See `PROTOCOL_TRACE.md` for the full packet-level walkthrough and
`COMPATIBILITY.md` for the SDK and protocol-era matrix.
