# MCP on the wire: a packet-level walkthrough

Generated from `mcp_stateless.pcap` by `06_pcap_to_markdown.py`. This is the traffic produced by `04_client_demo.py`, so every exchange below corresponds to something in that script.

## What to notice

**1. There is no handshake.** The very first message is `server/discover`, and the client is already sending real requests immediately after. The old `initialize` / `initialized` exchange is gone.

**2. No session header anywhere.** Search the whole trace for `Mcp-Session-Id` and you will not find it. The 2026-07-28 protocol removed it, which is what lets any replica answer any request.

**3. Every request is self-describing.** Each one repeats its `_meta` block with `protocolVersion`, `clientInfo` and `clientCapabilities`. That is the cost of statelessness: a little more on the wire, in exchange for no server-side memory.

**4. Routing headers mirror the body.** `mcp-protocol-version`, `mcp-method` and `mcp-name` (the tool being called) appear as HTTP headers, so a gateway or load balancer can route on them without parsing the JSON-RPC payload. See the load-balancing note below for what is deliberately *not* there.

**5. State is a plain argument.** In the `03` section, `session_id` travels inside `arguments` like any other parameter. That is the whole explicit-handle pattern, visible in the bytes. Watch one session count 1, 2, 3 while a second session independently starts at 1.

## Two questions this trace always raises

**Why are there three near-identical `add` calls in section `01`?** Compare any two of them: they differ only in `id` and `progressToken`. Everything else, including `Content-Length`, is identical, and both return `5.0`. Those two fields are correlation handles, not state: `id` is how JSON-RPC matches a response to its request, and `progressToken` is the label a server would attach progress notifications to. Repeating the call is the point. Same input, same output, nothing accumulating on the server. Compare with section `02`, where three identical `increment` calls return 1, 2 and 3.

**Why does `tools/list` come *after* the first `tools/call`?** The order is discover, call, list, call, call, which looks backwards. Note first that `04_client_demo.py` never asks for a tool list at all: it only calls `add` three times. That request is emitted by the fastmcp client itself, from inside the code that parses a call result:

```python
# fastmcp/client/mixins/tools.py
# Ensure the schema cache is populated for type validation.
if name not in tool_output_schemas:
    await list_tools_fn()
```

So the sequence is: send the first call, get the response back, and *while deserializing it* discover that the tool's output schema is not cached yet. The client fetches the schema then, uses it to coerce `structuredContent` into a typed value, and keeps it for the rest of the connection. Calls two and three skip the fetch.

Two things worth taking from that. It is a **client library detail, not a protocol rule**: another SDK is free to list tools up front. And the cache lives in the **client**, so the server still stores nothing between requests. Client-side caching and a stateless server are not in conflict.

## Load balancing: why the session is *not* in a header

Reading the trace, a lot is exposed in headers for routing, but the session id is not. In section `03` it appears only inside the JSON body, as a normal `session_id` entry in `arguments`. That is deliberate.

Routing on a session is exactly the sticky-session behaviour the 2026-07-28 spec set out to remove. Under the old model the session lived in one process's memory, so the balancer *had* to pin a client to the replica that held it. Under the new model the session id is just a lookup key into a shared store, so any replica can serve any request and there is nothing to route on. Plain round-robin is the point.

**If you do need it in a header, there is a supported opt-in.** Annotate the argument in the tool's input schema with `x-mcp-header` and the client mirrors it into an `Mcp-Param-<token>` header:

```python
from typing import Annotated
from pydantic import Field

@mcp.tool
async def increment(
    session_id: Annotated[
        SessionId,
        Field(json_schema_extra={"x-mcp-header": "session"}),
    ]
) -> int:
    ...
```

which puts this on the wire, alongside the value in the body:

```http
POST /mcp HTTP/1.1
mcp-method: tools/call
mcp-name: increment
Mcp-Param-session: 05fb378c-3e69-4e28-8b7e-c8e68d50d4a4
```

The server validates that the header agrees with the body, so the two cannot drift apart.

### But something still has to know about the state

Correct, and this is the honest version of the story. Statelessness does not delete the state, it **moves** it. What actually changed is narrower than the marketing suggests:

| | Sticky sessions (old) | Shared store (new) |
|---|---|---|
| Where state lives | one replica's memory | a store both replicas read |
| Routing is | a **correctness** requirement | irrelevant to correctness |
| Route to the wrong replica | session lost, request broken | works fine |
| Replica restarts | sessions on it are gone | unaffected |
| Cost | LB config coupled to app state | you now operate a store |

So the load balancer does not need to be state-aware, but your *architecture* still does. You traded routing complexity for storage complexity: an extra network hop on stateful calls, a new dependency and failure domain, and a retention policy to choose. FastMCP writes never set a TTL themselves, so expiry is entirely the store's (`redis`, `valkey`, `postgresql`, `dynamodb`, `mongodb`, `memcached` and others are available through the `AsyncKeyValue` interface).

**One caveat worth knowing before you fan out.** FastMCP's own docstring for `Session` says: *"Concurrent writes to one session race on the read-modify-write; session state is small and typically driven serially by one agent, so this is acceptable."* That is an assumption, not a guarantee. Two requests for the same session landing on two replicas at the same moment can lose an update, because `get` then `set` is not atomic. Fine for one agent working through a conversation, not fine for concurrent fan-out over a shared session. If you need that, use a store with atomic operations or keep writes serialised per session.

This is also where session-aware routing earns its place. Hashing on `Mcp-Param-session` gives you cache locality and narrows that race window, but as an **optimisation**: if the routing misses, the request is still correct, just a store round-trip slower. That is the real difference from sticky sessions, where a routing miss was a bug.

### One endpoint, two protocols at once

A modern server accepts both eras, so at any moment a single endpoint may be serving a mix, and every gateway, load balancer, WAF or tracing tool in front of it sees that mix too. Measured by pointing a modern client (`mcp 2.0.0`) and a legacy one (`mcp 1.29.0`) at the same server and capturing on one port:

```
3 requests   mcp-protocol-version: 2026-07-28   + mcp-method + mcp-name
3 requests   mcp-protocol-version: 2025-11-25   (no routing headers)
```

**The routing headers are modern-only.** `mcp-method` and `mcp-name` appeared on the modern client's requests and on none of the legacy ones. Any rule that routes on them silently covers only part of your traffic; for legacy clients an intermediary still has to parse the JSON-RPC body to know which method is being called.

Worse for classification: the legacy client's very first request, the `initialize`, carried **no** `mcp-protocol-version` header at all, because the version is what that handshake is negotiating. So the one request that opens a legacy conversation is also the one an intermediary cannot classify from headers alone.

Practical guidance for anything sitting in front of MCP servers: treat header-based routing as an optimisation with a body-parsing or default-route fallback, never as complete coverage. And expect the mix to shift under you. The era a client speaks follows its `mcp` library version, so a dependency bump on the client side changes what your infrastructure sees, with no server change at all.

### What if the client is modern but the server is legacy?

The client probes, fails, and falls back. Measured by pointing a modern `fastmcp 4.0.0b1` client at a legacy-era server (`fastmcp 3.4.7` on `mcp 1.29.0`):

```
-> server/discover              mcp-protocol-version: 2026-07-28   # modern probe
<- HTTP 400  Bad Request: Missing session ID                       # probe refused
-> initialize                   (no version header)                # fall back
<- HTTP 200  + Mcp-Session-Id: 8cf4445861264f71801836a3ff86ee13
-> notifications/initialized    2025-11-25  + Mcp-Session-Id
-> tools/list                   2025-11-25  + Mcp-Session-Id
-> tools/call                   2025-11-25  + Mcp-Session-Id
```

It costs one wasted round trip and then behaves exactly like a legacy client. Note what the client's own code looked like: unchanged. The same `Client(url)` produced sessionless traffic against a modern server and full sticky-session traffic here.

**This is the part that matters for anything sitting in front of the servers.** You cannot infer wire behaviour from the client alone. A modern-capable client talking to a legacy server emits `Mcp-Session-Id` on every request after the handshake, so that conversation *does* need session affinity. Whether a given flow is sticky is a property of the negotiated pair, not of either end. If you run a mixed fleet, some of your traffic still needs the old treatment.

Two smaller details worth noticing in that capture. The fallback `initialize` carries no `mcp-protocol-version` header at all, so the request that starts the legacy conversation is again the one an intermediary cannot classify from headers. And the refused probe still came back carrying a session id of its own (`0621348bbee1473784df24603addd315`), different from the one the conversation went on to use: a rejected, unauthenticated probe was enough to make the server allocate session state. Worth a thought if you are sizing or defending a legacy-era deployment.

### Does this mean the load balancer needs a smarter algorithm?

It looks that way, but the opposite is true. The tempting conclusion is to build an era-detecting balancer: sniff headers, spot legacy flows, apply stickiness selectively, parse bodies when the headers are missing. Do not build that. Measuring which combinations actually produce a session id shows the variable you control is somewhere else:

| Negotiated era | Server `stateless_http` | `Mcp-Session-Id` | Affinity needed |
|---|---|---|---|
| modern | `True` | no | no |
| modern | `False` | no | no |
| legacy | `True` | **no** | **no** |
| legacy | `False` | yes | yes |

Only one cell needs sticky sessions, and the thing that selects it is **the server's own configuration, not the client**. A legacy client against a server running `stateless_http=True` produced zero session ids; the same client against a session-keeping server produced nine. Meanwhile the modern client produced zero against every server in this capture, whatever their setting, because the modern protocol has no session concept to use.

So the fix is not a cleverer algorithm, it is a boring fleet: run `stateless_http=True` everywhere and keep application state in a shared store. Then every flow, legacy or modern, is round-robin safe, and the balancer needs no MCP awareness at all. Complexity moves from runtime traffic inspection, which is fragile and silently era-dependent, to deployment configuration, which you can audit.

Reach for MCP-aware routing only when you want something extra: shard locality via `Mcp-Param-*`, per-tenant rate limiting, or richer telemetry. Those are optimisations layered on a fleet that is already correct without them.

### The limit of that argument: state you cannot serialise

Everything above assumes the state is **data**, something you can put in a key-value store. Drop that assumption and the simplicity goes with it. Run two replicas of server `03` with the default in-memory store, mint a handle on one, and send it to the other the way a round-robin balancer would:

```
replica A: created session, increment -> 1
replica B: FAILED -> ToolError: Invalid or unknown session.
```

The explicit-handle pattern does not make you round-robin safe by itself. It makes you round-robin safe *when the state behind the handle is genuinely shared*. Here the handle was valid and the store was process-local, so the second replica had never heard of it.

That is fixable with Redis. What is not fixable that way is state that is a **live resource** rather than data: an interpreter or REPL kernel with variables and imports in memory, a code-execution container, a headless browser context, an SSH session, an open transaction. You cannot serialise a running process into a key-value store, so requests carrying that handle have to reach the machine actually holding it.

Affinity comes back for those, and you get two honest choices. Route on the handle at the edge, promoting it with `x-mcp-header` so the balancer can hash on `Mcp-Param-*`. Or keep the MCP servers stateless and put the indirection behind them: any replica accepts the request, looks up which node owns that container, and proxies. The first puts the mapping in your infrastructure, the second in your application. Neither is free, and no protocol version removes the constraint.

**The modern spec still helps here, just not by deleting the problem.** Under legacy, affinity keyed on an `Mcp-Session-Id` the transport minted and tied to a connection. Now you mint the handle yourself: you choose what it identifies, how long it lives, when it dies (`end_session` becomes "destroy the container"), and whether it is exposed for routing. Explicit, application-owned affinity is easier to reason about and to bound than implicit transport affinity, even though it is still affinity. What you inherit in exchange is a resource lifecycle to run: TTLs, eviction, orphan cleanup after a node dies.

So the honest rule. If your state is data, statelessness really does buy you a dumb balancer. If your state is a live resource, it buys you an explicit handle to route on instead of a hidden one, and you still owe the system somewhere that knows where things live. The trap in both cases is the same: needing traffic inspection for *correctness*. Route deliberately on a handle you designed, not by sniffing which protocol era a flow happened to negotiate.

### Scaling: the shared store fixes one bug and introduces another

Two replicas with the default in-memory store reject each other's handles, so the fix is a shared store. That works, and it brings a new problem that is much harder to notice. Same two replicas, now sharing one store, with increments fired concurrently and alternated between them the way a round-robin balancer would:

```
replica B accepts the handle -> 1            # shared store works
20 concurrent increments across 2 replicas -> counter 17  (LOST 3)
50 concurrent increments across 2 replicas -> counter 46  (LOST 4)
```

**Every one of those calls succeeded.** No exception, no error response, no warning in a log. The counter is simply wrong. `get` then `set` is a read-modify-write with a network round trip in the middle, so two replicas read the same value and the second write erases the first.

FastMCP is upfront about this in the `Session` docstring: *"Concurrent writes to one session race on the read-modify-write; session state is small and typically driven serially by one agent, so this is acceptable."* That is a reasonable assumption for one agent working through a conversation. It is exactly the assumption that scale removes: parallel tool calls, an agent fanning out subtasks, or two user actions arriving together will all break it, and they break it silently.

Three ways out, roughly in order of preference. Design session writes to be **commutative or idempotent** so interleaving cannot lose anything, which is a modelling change rather than an infrastructure one. Use a store with **atomic primitives** and push the operation into it, for example a Redis `INCR` or a compare-and-swap loop, rather than reading into Python and writing back. Or **serialise per session**, with a lock or by routing a session's requests to one worker, which reintroduces exactly the affinity you removed.

The general shape is worth naming, because it is the part that actually bites when you scale MCP servers. Statelessness moved the state into a shared store, and the moment more than one replica writes to the same key you have inherited a distributed systems problem complete with lost updates, retention policy and a new failure domain. The protocol made routing simple. It did not make concurrency simple, and the default API shape (`get` then `set`) quietly invites the bug.


> Captured on loopback with a full snaplen (`tcpdump -i lo0 -s 0`). A truncated snaplen silently cuts off the larger `tools/list` responses.


---

## 01 stateless

`127.0.0.1:8001` (stateless_http=True, no session at all)


### 1. server/discover

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8001
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: server/discover
Content-Length: 245

{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "server/discover",
  "params": {
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {}
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 417
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "cacheScope": "private",
    "capabilities": {
      "extensions": {
        "io.modelcontextprotocol/ui": {}
      },
      "logging": {},
      "prompts": {
        "listChanged": false
      },
      "resources": {
        "listChanged": false,
        "subscribe": false
      },
      "tools": {
        "listChanged": false
      }
    },
    "resultType": "complete",
    "supportedVersions": [
      "2026-07-28"
    ],
    "ttlMs": 0,
    "_meta": {
      "io.modelcontextprotocol/serverInfo": {
        "name": "stateless-calculator",
        "version": "4.0.0b1"
      }
    }
  }
}
```


### 2. tools/call (add)

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8001
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: tools/call
mcp-name: add
Content-Length: 340

{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "add",
    "arguments": {
      "a": 2,
      "b": 3
    },
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/logLevel": "debug",
      "progressToken": 2
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 281
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "_meta": {
      "fastmcp": {
        "wrap_result": true
      },
      "io.modelcontextprotocol/serverInfo": {
        "name": "stateless-calculator",
        "version": "4.0.0b1"
      }
    },
    "content": [
      {
        "text": "5.0",
        "type": "text"
      }
    ],
    "isError": false,
    "resultType": "complete",
    "structuredContent": {
      "result": 5.0
    }
  }
}
```


### 3. tools/list

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8001
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: tools/list
Content-Length: 283

{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/list",
  "params": {
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/logLevel": "debug"
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 1364
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "cacheScope": "private",
    "resultType": "complete",
    "tools": [
      {
        "_meta": {
          "fastmcp": {
            "tags": []
          }
        },
        "description": "Add two numbers. The result depends ONLY on a and b.",
        "inputSchema": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "a": {
              "type": "number"
            },
            "b": {
              "type": "number"
            }
          },
          "required": [
            "a",
            "b"
          ]
        },
        "name": "add",
        "outputSchema": {
          "properties": {
            "result": {
              "type": "number"
            }
          },
          "required": [
            "result"
          ],
          "type": "object",
          "x-fastmcp-wrap-result": true
        }
      },
      {
        "_meta": {
          "fastmcp": {
            "tags": []
          }
        },
        "description": "Multiply two numbers. Same inputs -> always the same output.",
        "inputSchema": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "a": {
              "type": "number"
            },
            "b": {
              "type": "number"
            }
          },
          "required": [
            "a",
            "b"
          ]
        },
        "name": "multiply",
        "outputSchema": {
          "properties": {
            "result": {
              "type": "number"
            }
          },
          "required": [
            "result"
          ],
          "type": "object",
          "x-fastmcp-wrap-result": true
        }
      },
      {
        "_meta": {
          "fastmcp": {
            "tags": []
          }
        },
        "description": "Convert a temperature. No hidden memory involved.",
        "inputSchema": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "celsius": {
              "type": "number"
            }
          },
          "required": [
            "celsius"
          ]
        },
        "name": "celsius_to_fahrenheit",
        "outputSchema": {
          "properties": {
            "result": {
              "type": "number"
            }
          },
          "required": [
            "result"
          ],
          "type": "object",
          "x-fastmcp-wrap-result": true
        }
      }
    ],
    "ttlMs": 0,
    "_meta": {
      "io.modelcontextprotocol/serverInfo": {
        "name": "stateless-calculator",
        "version": "4.0.0b1"
      }
    }
  }
}
```


### 4. tools/call (add)

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8001
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: tools/call
mcp-name: add
Content-Length: 340

{
  "jsonrpc": "2.0",
  "id": 4,
  "method": "tools/call",
  "params": {
    "name": "add",
    "arguments": {
      "a": 2,
      "b": 3
    },
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/logLevel": "debug",
      "progressToken": 4
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 281
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 4,
  "result": {
    "_meta": {
      "fastmcp": {
        "wrap_result": true
      },
      "io.modelcontextprotocol/serverInfo": {
        "name": "stateless-calculator",
        "version": "4.0.0b1"
      }
    },
    "content": [
      {
        "text": "5.0",
        "type": "text"
      }
    ],
    "isError": false,
    "resultType": "complete",
    "structuredContent": {
      "result": 5.0
    }
  }
}
```


### 5. tools/call (add)

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8001
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: tools/call
mcp-name: add
Content-Length: 340

{
  "jsonrpc": "2.0",
  "id": 5,
  "method": "tools/call",
  "params": {
    "name": "add",
    "arguments": {
      "a": 2,
      "b": 3
    },
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/logLevel": "debug",
      "progressToken": 5
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 281
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 5,
  "result": {
    "_meta": {
      "fastmcp": {
        "wrap_result": true
      },
      "io.modelcontextprotocol/serverInfo": {
        "name": "stateless-calculator",
        "version": "4.0.0b1"
      }
    },
    "content": [
      {
        "text": "5.0",
        "type": "text"
      }
    ],
    "isError": false,
    "resultType": "complete",
    "structuredContent": {
      "result": 5.0
    }
  }
}
```


---

## 02 stateful (global)

`127.0.0.1:8002` (one counter shared by every client)


### 1. server/discover

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8002
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: server/discover
Content-Length: 245

{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "server/discover",
  "params": {
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {}
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 414
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "cacheScope": "private",
    "capabilities": {
      "extensions": {
        "io.modelcontextprotocol/ui": {}
      },
      "logging": {},
      "prompts": {
        "listChanged": false
      },
      "resources": {
        "listChanged": false,
        "subscribe": false
      },
      "tools": {
        "listChanged": false
      }
    },
    "resultType": "complete",
    "supportedVersions": [
      "2026-07-28"
    ],
    "ttlMs": 0,
    "_meta": {
      "io.modelcontextprotocol/serverInfo": {
        "name": "stateful-notebook",
        "version": "4.0.0b1"
      }
    }
  }
}
```


### 2. tools/call (increment)

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8002
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: tools/call
mcp-name: increment
Content-Length: 335

{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "increment",
    "arguments": {},
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/logLevel": "debug",
      "progressToken": 2
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 274
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "_meta": {
      "fastmcp": {
        "wrap_result": true
      },
      "io.modelcontextprotocol/serverInfo": {
        "name": "stateful-notebook",
        "version": "4.0.0b1"
      }
    },
    "content": [
      {
        "text": "1",
        "type": "text"
      }
    ],
    "isError": false,
    "resultType": "complete",
    "structuredContent": {
      "result": 1
    }
  }
}
```


### 3. tools/list

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8002
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: tools/list
Content-Length: 283

{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/list",
  "params": {
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/logLevel": "debug"
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 1676
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "cacheScope": "private",
    "resultType": "complete",
    "tools": [
      {
        "_meta": {
          "fastmcp": {
            "tags": []
          }
        },
        "description": "Increase a shared counter and return its new value.\n\nCall it repeatedly and the number keeps growing: the server is\nremembering the previous value between calls.",
        "inputSchema": {
          "type": "object",
          "additionalProperties": false,
          "properties": {}
        },
        "name": "increment",
        "outputSchema": {
          "properties": {
            "result": {
              "type": "integer"
            }
          },
          "required": [
            "result"
          ],
          "type": "object",
          "x-fastmcp-wrap-result": true
        }
      },
      {
        "_meta": {
          "fastmcp": {
            "tags": []
          }
        },
        "description": "Read the current counter without changing it.",
        "inputSchema": {
          "type": "object",
          "additionalProperties": false,
          "properties": {}
        },
        "name": "get_counter",
        "outputSchema": {
          "properties": {
            "result": {
              "type": "integer"
            }
          },
          "required": [
            "result"
          ],
          "type": "object",
          "x-fastmcp-wrap-result": true
        }
      },
      {
        "_meta": {
          "fastmcp": {
            "tags": []
          }
        },
        "description": "Store a note. It stays in memory for later calls.",
        "inputSchema": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "text": {
              "type": "string"
            }
          },
          "required": [
            "text"
          ]
        },
        "name": "add_note",
        "outputSchema": {
          "properties": {
            "result": {
              "type": "string"
            }
          },
          "required": [
            "result"
          ],
          "type": "object",
          "x-fastmcp-wrap-result": true
        }
      },
      {
        "_meta": {
          "fastmcp": {
            "tags": []
          }
        },
        "description": "Return every note stored so far in this server process.",
        "inputSchema": {
          "type": "object",
          "additionalProperties": false,
          "properties": {}
        },
        "name": "list_notes",
        "outputSchema": {
          "properties": {
            "result": {
              "items": {
                "type": "string"
              },
              "type": "array"
            }
          },
          "required": [
            "result"
          ],
          "type": "object",
          "x-fastmcp-wrap-result": true
        }
      }
    ],
    "ttlMs": 0,
    "_meta": {
      "io.modelcontextprotocol/serverInfo": {
        "name": "stateful-notebook",
        "version": "4.0.0b1"
      }
    }
  }
}
```


### 4. tools/call (increment)

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8002
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: tools/call
mcp-name: increment
Content-Length: 335

{
  "jsonrpc": "2.0",
  "id": 4,
  "method": "tools/call",
  "params": {
    "name": "increment",
    "arguments": {},
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/logLevel": "debug",
      "progressToken": 4
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 274
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 4,
  "result": {
    "_meta": {
      "fastmcp": {
        "wrap_result": true
      },
      "io.modelcontextprotocol/serverInfo": {
        "name": "stateful-notebook",
        "version": "4.0.0b1"
      }
    },
    "content": [
      {
        "text": "2",
        "type": "text"
      }
    ],
    "isError": false,
    "resultType": "complete",
    "structuredContent": {
      "result": 2
    }
  }
}
```


### 5. tools/call (increment)

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8002
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: tools/call
mcp-name: increment
Content-Length: 335

{
  "jsonrpc": "2.0",
  "id": 5,
  "method": "tools/call",
  "params": {
    "name": "increment",
    "arguments": {},
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/logLevel": "debug",
      "progressToken": 5
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 274
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 5,
  "result": {
    "_meta": {
      "fastmcp": {
        "wrap_result": true
      },
      "io.modelcontextprotocol/serverInfo": {
        "name": "stateful-notebook",
        "version": "4.0.0b1"
      }
    },
    "content": [
      {
        "text": "3",
        "type": "text"
      }
    ],
    "isError": false,
    "resultType": "complete",
    "structuredContent": {
      "result": 3
    }
  }
}
```


### 6. tools/call (add_note)

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8002
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: tools/call
mcp-name: add_note
Content-Length: 351

{
  "jsonrpc": "2.0",
  "id": 6,
  "method": "tools/call",
  "params": {
    "name": "add_note",
    "arguments": {
      "text": "buy milk"
    },
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/logLevel": "debug",
      "progressToken": 6
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 326
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 6,
  "result": {
    "_meta": {
      "fastmcp": {
        "wrap_result": true
      },
      "io.modelcontextprotocol/serverInfo": {
        "name": "stateful-notebook",
        "version": "4.0.0b1"
      }
    },
    "content": [
      {
        "text": "Stored note #1: 'buy milk'",
        "type": "text"
      }
    ],
    "isError": false,
    "resultType": "complete",
    "structuredContent": {
      "result": "Stored note #1: 'buy milk'"
    }
  }
}
```


### 7. tools/call (add_note)

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8002
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: tools/call
mcp-name: add_note
Content-Length: 353

{
  "jsonrpc": "2.0",
  "id": 7,
  "method": "tools/call",
  "params": {
    "name": "add_note",
    "arguments": {
      "text": "call Alice"
    },
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/logLevel": "debug",
      "progressToken": 7
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 330
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 7,
  "result": {
    "_meta": {
      "fastmcp": {
        "wrap_result": true
      },
      "io.modelcontextprotocol/serverInfo": {
        "name": "stateful-notebook",
        "version": "4.0.0b1"
      }
    },
    "content": [
      {
        "text": "Stored note #2: 'call Alice'",
        "type": "text"
      }
    ],
    "isError": false,
    "resultType": "complete",
    "structuredContent": {
      "result": "Stored note #2: 'call Alice'"
    }
  }
}
```


### 8. tools/call (list_notes)

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8002
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: tools/call
mcp-name: list_notes
Content-Length: 336

{
  "jsonrpc": "2.0",
  "id": 8,
  "method": "tools/call",
  "params": {
    "name": "list_notes",
    "arguments": {},
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/logLevel": "debug",
      "progressToken": 8
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 326
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 8,
  "result": {
    "_meta": {
      "fastmcp": {
        "wrap_result": true
      },
      "io.modelcontextprotocol/serverInfo": {
        "name": "stateful-notebook",
        "version": "4.0.0b1"
      }
    },
    "content": [
      {
        "text": "[\"buy milk\",\"call Alice\"]",
        "type": "text"
      }
    ],
    "isError": false,
    "resultType": "complete",
    "structuredContent": {
      "result": [
        "buy milk",
        "call Alice"
      ]
    }
  }
}
```


---

## 03 stateful (per-session)

`127.0.0.1:8003` (explicit session-id handles)


### 1. server/discover

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8003
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: server/discover
Content-Length: 245

{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "server/discover",
  "params": {
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {}
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 417
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "cacheScope": "private",
    "capabilities": {
      "extensions": {
        "io.modelcontextprotocol/ui": {}
      },
      "logging": {},
      "prompts": {
        "listChanged": false
      },
      "resources": {
        "listChanged": false,
        "subscribe": false
      },
      "tools": {
        "listChanged": false
      }
    },
    "resultType": "complete",
    "supportedVersions": [
      "2026-07-28"
    ],
    "ttlMs": 0,
    "_meta": {
      "io.modelcontextprotocol/serverInfo": {
        "name": "per-session-notebook",
        "version": "4.0.0b1"
      }
    }
  }
}
```


### 2. tools/call (create_session)

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8003
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: tools/call
mcp-name: create_session
Content-Length: 340

{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "create_session",
    "arguments": {},
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/logLevel": "debug",
      "progressToken": 2
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 349
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "_meta": {
      "fastmcp": {
        "wrap_result": true
      },
      "io.modelcontextprotocol/serverInfo": {
        "name": "per-session-notebook",
        "version": "4.0.0b1"
      }
    },
    "content": [
      {
        "text": "32fd291f-e091-4210-a8af-8b029fb6b1e8",
        "type": "text"
      }
    ],
    "isError": false,
    "resultType": "complete",
    "structuredContent": {
      "result": "32fd291f-e091-4210-a8af-8b029fb6b1e8"
    }
  }
}
```


### 3. tools/list

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8003
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: tools/list
Content-Length: 283

{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/list",
  "params": {
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/logLevel": "debug"
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 3313
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "cacheScope": "private",
    "resultType": "complete",
    "tools": [
      {
        "_meta": {
          "fastmcp": {
            "tags": []
          }
        },
        "description": "Increment a counter that belongs to THIS session only.",
        "inputSchema": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "session_id": {
              "type": "string",
              "description": "Session identifier. Use a tool to create a session, then pass the resulting id here to persist state across calls in the same session."
            }
          },
          "required": [
            "session_id"
          ]
        },
        "name": "increment",
        "outputSchema": {
          "properties": {
            "result": {
              "type": "integer"
            }
          },
          "required": [
            "result"
          ],
          "type": "object",
          "x-fastmcp-wrap-result": true
        }
      },
      {
        "_meta": {
          "fastmcp": {
            "tags": []
          }
        },
        "description": "Store a fact in THIS session's memory.",
        "inputSchema": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "session_id": {
              "type": "string",
              "description": "Session identifier. Use a tool to create a session, then pass the resulting id here to persist state across calls in the same session."
            },
            "fact": {
              "type": "string"
            }
          },
          "required": [
            "session_id",
            "fact"
          ]
        },
        "name": "remember",
        "outputSchema": {
          "properties": {
            "result": {
              "type": "string"
            }
          },
          "required": [
            "result"
          ],
          "type": "object",
          "x-fastmcp-wrap-result": true
        }
      },
      {
        "_meta": {
          "fastmcp": {
            "tags": []
          }
        },
        "description": "Return everything remembered in THIS session.",
        "inputSchema": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "session_id": {
              "type": "string",
              "description": "Session identifier. Use a tool to create a session, then pass the resulting id here to persist state across calls in the same session."
            }
          },
          "required": [
            "session_id"
          ]
        },
        "name": "recall",
        "outputSchema": {
          "properties": {
            "result": {
              "items": {
                "type": "string"
              },
              "type": "array"
            }
          },
          "required": [
            "result"
          ],
          "type": "object",
          "x-fastmcp-wrap-result": true
        }
      },
      {
        "_meta": {
          "fastmcp": {
            "tags": []
          }
        },
        "description": "Create a new session and return its identifier.\n\nMints an unguessable `uuid4`, records an initial session owned by the current\nprincipal, and returns the id as a string. Store it and pass it back as a\n`session_id` argument on later calls to persist state across a session \u2014 only\nan id created this way resolves. State is keyed by the authenticated\nprincipal, so the id organizes sessions within a user; on an unauthenticated\nconnection the id is the only thing standing between callers, which is why it\nis unguessable.",
        "inputSchema": {
          "type": "object",
          "additionalProperties": false,
          "properties": {}
        },
        "name": "create_session",
        "outputSchema": {
          "properties": {
            "result": {
              "type": "string"
            }
          },
          "required": [
            "result"
          ],
          "type": "object",
          "x-fastmcp-wrap-result": true
        }
      },
      {
        "_meta": {
          "fastmcp": {
            "tags": []
          }
        },
        "description": "End a session and delete all of its state.\n\nValidates the id like any other resolution (an unknown or foreign id is\nrejected), then deletes the session's key so the id no longer resolves.",
        "inputSchema": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "session_id": {
              "type": "string",
              "description": "Session identifier. Use a tool to create a session, then pass the resulting id here to persist state across calls in the same session."
            }
          },
          "required": [
            "session_id"
          ]
        },
        "name": "end_session",
        "outputSchema": {
          "properties": {
            "result": {
              "type": "string"
            }
          },
          "required": [
            "result"
          ],
          "type": "object",
          "x-fastmcp-wrap-result": true
        }
      }
    ],
    "ttlMs": 0,
    "_meta": {
      "io.modelcontextprotocol/serverInfo": {
        "name": "per-session-notebook",
        "version": "4.0.0b1"
      }
    }
  }
}
```


### 4. tools/call (create_session)

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8003
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: tools/call
mcp-name: create_session
Content-Length: 340

{
  "jsonrpc": "2.0",
  "id": 4,
  "method": "tools/call",
  "params": {
    "name": "create_session",
    "arguments": {},
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/logLevel": "debug",
      "progressToken": 4
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 349
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 4,
  "result": {
    "_meta": {
      "fastmcp": {
        "wrap_result": true
      },
      "io.modelcontextprotocol/serverInfo": {
        "name": "per-session-notebook",
        "version": "4.0.0b1"
      }
    },
    "content": [
      {
        "text": "f418df72-2c37-4acb-b06b-d237f6ce10a6",
        "type": "text"
      }
    ],
    "isError": false,
    "resultType": "complete",
    "structuredContent": {
      "result": "f418df72-2c37-4acb-b06b-d237f6ce10a6"
    }
  }
}
```


### 5. tools/call (increment)

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8003
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: tools/call
mcp-name: increment
Content-Length: 386

{
  "jsonrpc": "2.0",
  "id": 5,
  "method": "tools/call",
  "params": {
    "name": "increment",
    "arguments": {
      "session_id": "32fd291f-e091-4210-a8af-8b029fb6b1e8"
    },
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/logLevel": "debug",
      "progressToken": 5
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 277
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 5,
  "result": {
    "_meta": {
      "fastmcp": {
        "wrap_result": true
      },
      "io.modelcontextprotocol/serverInfo": {
        "name": "per-session-notebook",
        "version": "4.0.0b1"
      }
    },
    "content": [
      {
        "text": "1",
        "type": "text"
      }
    ],
    "isError": false,
    "resultType": "complete",
    "structuredContent": {
      "result": 1
    }
  }
}
```


### 6. tools/call (increment)

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8003
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: tools/call
mcp-name: increment
Content-Length: 386

{
  "jsonrpc": "2.0",
  "id": 6,
  "method": "tools/call",
  "params": {
    "name": "increment",
    "arguments": {
      "session_id": "32fd291f-e091-4210-a8af-8b029fb6b1e8"
    },
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/logLevel": "debug",
      "progressToken": 6
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 277
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 6,
  "result": {
    "_meta": {
      "fastmcp": {
        "wrap_result": true
      },
      "io.modelcontextprotocol/serverInfo": {
        "name": "per-session-notebook",
        "version": "4.0.0b1"
      }
    },
    "content": [
      {
        "text": "2",
        "type": "text"
      }
    ],
    "isError": false,
    "resultType": "complete",
    "structuredContent": {
      "result": 2
    }
  }
}
```


### 7. tools/call (increment)

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8003
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: tools/call
mcp-name: increment
Content-Length: 386

{
  "jsonrpc": "2.0",
  "id": 7,
  "method": "tools/call",
  "params": {
    "name": "increment",
    "arguments": {
      "session_id": "32fd291f-e091-4210-a8af-8b029fb6b1e8"
    },
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/logLevel": "debug",
      "progressToken": 7
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 277
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 7,
  "result": {
    "_meta": {
      "fastmcp": {
        "wrap_result": true
      },
      "io.modelcontextprotocol/serverInfo": {
        "name": "per-session-notebook",
        "version": "4.0.0b1"
      }
    },
    "content": [
      {
        "text": "3",
        "type": "text"
      }
    ],
    "isError": false,
    "resultType": "complete",
    "structuredContent": {
      "result": 3
    }
  }
}
```


### 8. tools/call (increment)

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8003
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: tools/call
mcp-name: increment
Content-Length: 386

{
  "jsonrpc": "2.0",
  "id": 8,
  "method": "tools/call",
  "params": {
    "name": "increment",
    "arguments": {
      "session_id": "f418df72-2c37-4acb-b06b-d237f6ce10a6"
    },
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/logLevel": "debug",
      "progressToken": 8
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 277
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 8,
  "result": {
    "_meta": {
      "fastmcp": {
        "wrap_result": true
      },
      "io.modelcontextprotocol/serverInfo": {
        "name": "per-session-notebook",
        "version": "4.0.0b1"
      }
    },
    "content": [
      {
        "text": "1",
        "type": "text"
      }
    ],
    "isError": false,
    "resultType": "complete",
    "structuredContent": {
      "result": 1
    }
  }
}
```


### 9. tools/call (increment)

**Request**

```http
POST /mcp HTTP/1.1
Host: 127.0.0.1:8003
accept: application/json, text/event-stream
content-type: application/json
mcp-protocol-version: 2026-07-28
mcp-method: tools/call
mcp-name: increment
Content-Length: 386

{
  "jsonrpc": "2.0",
  "id": 9,
  "method": "tools/call",
  "params": {
    "name": "increment",
    "arguments": {
      "session_id": "32fd291f-e091-4210-a8af-8b029fb6b1e8"
    },
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {},
      "io.modelcontextprotocol/logLevel": "debug",
      "progressToken": 9
    }
  }
}
```

**Response**

```http
HTTP/1.1 200
content-length: 277
content-type: application/json

{
  "jsonrpc": "2.0",
  "id": 9,
  "result": {
    "_meta": {
      "fastmcp": {
        "wrap_result": true
      },
      "io.modelcontextprotocol/serverInfo": {
        "name": "per-session-notebook",
        "version": "4.0.0b1"
      }
    },
    "content": [
      {
        "text": "4",
        "type": "text"
      }
    ],
    "isError": false,
    "resultType": "complete",
    "structuredContent": {
      "result": 4
    }
  }
}
```
