# SDK compatibility and backward compatibility

Status: **August 2026**. MCP is mid-migration between two protocol eras, so
"which SDK works with which server" is a live question. Everything in the
verified tables below was measured on this machine, not taken from a blog
post; reproduce it with `05_compat_check.py`.

## The two eras

| | Legacy era | Modern era |
|---|---|---|
| Spec date | `2025-11-25` | `2026-07-28` |
| Handshake | `initialize` / `initialized` | none, every request self-describing |
| Session | `Mcp-Session-Id` header, sticky | no protocol session |
| State | hidden in the transport | explicit handles passed as tool arguments |
| Load balancing | sticky sessions / shared session store | plain round-robin |

The 2026-07-28 release removed the handshake and the `Mcp-Session-Id` header
(SEP-2575, SEP-2567). Hence this training: state didn't disappear, it became
*explicit*.

## Where the SDKs actually stand

Versions read from PyPI/npm on 2026-08-01:

| SDK | Latest | MCP SDK pin | Era |
|---|---|---|---|
| `mcp` (official Python) | **2.0.0** | n/a | modern |
| `@modelcontextprotocol/server` / `client` (TS) | **2.0.0** | n/a | modern |
| `@modelcontextprotocol/sdk` (old TS package) | **1.30.0** | n/a | legacy |
| `fastmcp` (stable) | **3.4.5** | n/a | legacy-era default |
| `fastmcp` (beta, used here) | **4.0.0b1** | n/a | modern + legacy |
| `openai-agents` | **0.19.2** | `mcp<2,>=1.19.0` | **legacy only** |
| `agent-framework-core` (Microsoft) | **1.13.0** | `mcp<2,>=1.24.0` | **legacy only** |

The two rows that matter most in practice: **the OpenAI Agents SDK and
Microsoft Agent Framework are both still pinned to `mcp<2`.** They cannot
install the modern SDK at all. If your server only spoke the 2026-07-28
protocol, neither framework could talk to it today.

That is exactly why FastMCP 4's dual-era support isn't a nicety; it's what
keeps your server usable by the current agent ecosystem.

Officially, the four Tier 1 SDKs (TypeScript, Python, Go, C#) support
2026-07-28, with Rust in beta. Go shipped as `1.7.0-pre.1` and C# behind
`--prerelease` during the beta window.

## Verified: legacy clients still work

The claim "v2 servers continue to accept legacy 2025-11-25 handshake
requests" is testable, so it was tested. The same three servers were probed
from two isolated environments:

```
MODERN  mcp 2.0.0  + fastmcp 4.0.0b1        -> 3/3 servers OK
LEGACY  mcp 1.29.0 + openai-agents 0.19.2   -> 3/3 servers OK
```

Detail from the legacy-era run against FastMCP 4.0.0b1 servers:

```
[OK] 01 stateless        tools=['add','multiply','celsius_to_fahrenheit']  add(2,3)=5.0
[OK] 02 stateful-global  tools=['increment','get_counter','add_note','list_notes']
[OK] 03 stateful-session tools=['increment','remember','recall','create_session','end_session']
       session increment -> 1
       session increment -> 2
```

Two conclusions worth teaching:

1. **A FastMCP 4 server is reachable from legacy-era clients**, including
   the stateless one. You do not have to choose a side.
2. **The explicit-handle pattern is protocol-agnostic.** Server `03` kept
   correct per-session state (1, then 2) for a *legacy* client, because the
   session id travels as an ordinary tool argument. Nothing about it depends
   on the modern protocol, which is precisely why the spec could drop
   sessions from the transport without losing stateful behaviour.

Reproduce both runs:

```bash
python 05_compat_check.py                        # modern env

python -m venv /tmp/venv_legacy
/tmp/venv_legacy/bin/pip install openai-agents   # pins mcp<2
/tmp/venv_legacy/bin/python 05_compat_check.py   # legacy env
```

## How negotiation works in FastMCP 4

One deployment serves both eras, negotiating per connection:

```python
from fastmcp import Client

client = Client("https://example.com/mcp")                 # probes modern, falls back
legacy = Client("https://example.com/mcp", mode="legacy")  # pin the old protocol
```

Pin `mode="legacy"` only when you specifically need the session back-channel.

## Is sampling removed? No. Two different levels

This one trips people up, because the spec and the SDK made different calls.

**The protocol: deprecated, not removed.** Roots, Sampling and Logging are
still part of 2026-07-28. In the spec's own words they "still work, and
they'll keep working for at least twelve months". New code is discouraged
from adopting them, but nothing breaks today.

**FastMCP 4: the API is gone.** FastMCP went further than the spec and
removed three context methods outright, **from every protocol era**, so code
using them fails loudly at upgrade instead of degrading silently:

- `ctx.sample()`
- `ctx.sample_step()`
- `ctx.list_roots()`

Verified against 4.0.0b1: `ctx.sample`, `ctx.sample_step` and `ctx.list_roots`
are absent, while `ctx.elicit`, `ctx.report_progress` and `ctx.log` are still
there.

**The replacement** is MRTR (Multi Round-Trip Requests), which does what
sampling did but over a stateless protocol. A tool that needs something from
the user mid-call returns `resultType: "input_required"`; the client retries
the original call with `inputResponses` holding the answers. In FastMCP that
is returning an `InputRequiredResult` and reading `ctx.input_responses` on the
next round. Alternatively, just call an LLM directly from the server.

So: if you are on the protocol, you have a year. If you are on FastMCP 4, you
port to MRTR now.

One more consequence of statelessness, easy to hit in a demo: with
`stateless_http=True` there is no session and therefore no server→client
stream, so `GET /mcp` returns `405 Method Not Allowed`. Anything that relied
on server-initiated messages (streamed logs, progress, sampling) had to be
redesigned rather than simply ported.

## Deprecation timeline

- A formal deprecation policy with a **twelve-month minimum window**.
- Roots, Sampling and Logging are deprecated but keep working for **at least
  twelve months**.
- The legacy HTTP+SSE transport is formally deprecated with a **year-long
  offramp**.
- v1 SDKs receive bug and security patches for **at least six months** after
  the final release.

So there is no cliff. The practical reading for a team today: build new
servers on the modern era, keep accepting legacy clients, and treat anything
using sampling/roots as work to schedule inside the twelve-month window.

## Sources

- [The 2026-07-28 Specification](https://blog.modelcontextprotocol.io/posts/2026-07-28/)
- [2026-07-28 release candidate](https://blog.modelcontextprotocol.io/posts/2026-07-28-release-candidate/)
- [FastMCP 4, what's new](https://gofastmcp.com/getting-started/whats-new)
- [MCP 2026-07-28: stateless core, enterprise authorization, and SDK betas](https://fmind.medium.com/mcp-2026-07-28-stateless-spec-July-2026-2646a980d594)
- [Bringing MCP 2026-07-28 to Claude](https://claude.com/blog/bringing-mcp-2026-07-28-to-claude)
- [OpenAI Agents SDK, MCP](https://openai.github.io/openai-agents-python/mcp/)
- [Microsoft ships Agent Framework 1.0](https://visualstudiomagazine.com/articles/2026/04/06/microsoft-ships-production-ready-agent-framework-1-0-for-net-and-python.aspx)
- Version data read live from PyPI and the npm registry, 2026-08-01.
