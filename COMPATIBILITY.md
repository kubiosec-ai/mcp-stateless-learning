# SDK compatibility and backward compatibility

Status: **verified 2026-08-18**. MCP is mid-migration between two protocol eras, so
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

Versions read live from PyPI and npm on **2026-08-18**:

| SDK | Latest | MCP SDK pin | Era |
|---|---|---|---|
| `mcp` (official Python) | **2.0.0** | n/a | modern |
| `@modelcontextprotocol/server` / `client` (TS) | **2.0.0** | n/a | modern |
| `@modelcontextprotocol/sdk` (old TS package) | **1.30.0** | n/a | legacy |
| `fastmcp` (stable) | **3.4.7** | n/a | legacy-era default |
| `fastmcp` (beta, used here) | **4.0.0b1** | n/a | modern + legacy |
| `fastmcp` (newest beta) | **4.0.0b3** | n/a | modern + legacy |
| `openai-agents` | **0.21.1** | `mcp<3,>=1.19.0` | **modern-capable** |
| `agent-framework-core` (Microsoft) | **1.14.0** | `mcp<2,>=1.24.0` | **legacy only** |

**This is moving fast, so re-run `05_compat_check.py` rather than trusting
this table.** On 2026-08-01 both frameworks pinned `mcp<2` and neither could
install the modern SDK at all. Seventeen days later that is only half true:

- **OpenAI Agents SDK crossed over.** `0.19.2` pinned `mcp<2`; `0.21.1` pins
  `mcp<3`. A clean install now resolves to `mcp 2.0.0`, verified, so it speaks
  the modern stateless protocol.
- **Microsoft Agent Framework has not.** `agent-framework-core` `1.14.0` still
  pins `mcp<2,>=1.24.0`, so it remains legacy-era.

### What the OpenAI Agents SDK change actually means

"It can install `mcp 2.x`" is not the same as "it speaks the stateless
protocol", so this was measured on the wire against server `01`.

The Agents SDK does not implement the MCP protocol itself. It delegates to the
official `mcp` package, so **the protocol era is decided by whichever `mcp`
version your resolver picked**, not by the `openai-agents` version:

| openai-agents | resolved `mcp` | `mcp-protocol-version` sent | Handshake |
|---|---|---|---|
| 0.19.2 | 1.29.0 | `2025-11-25` | `initialize` + `notifications/initialized` |
| 0.21.1 | 2.0.0 | `2026-07-28` | none, starts at `server/discover` |
| 0.21.1 | 1.29.0 (forced) | `2025-11-25` | `initialize` + `notifications/initialized` |

That third row is the one to remember. Upgrading `openai-agents` does not by
itself move you to the stateless protocol; it only relaxes the pin so `mcp 2.x`
*may* be installed. In an environment that already holds `mcp 1.x`, the newest
Agents SDK still speaks the legacy era.

So, is it stateless "built in"? Yes, in the sense that there is no flag to set:
with `mcp 2.0.0` present the client sends `mcp-protocol-version: 2026-07-28`,
skips the handshake entirely, and emits `mcp-method` / `mcp-name` routing
headers, with zero `initialize` calls and no session id anywhere. Client code
is unchanged.

The flip side is worth planning for: the wire format changes underneath you on
a dependency bump. If anything in front of your servers inspects MCP traffic,
a routine `pip install -U` can change what it sees. Pin `mcp` deliberately
rather than letting the resolver decide.

Which is exactly why FastMCP 4's dual-era support is not a nicety. As long as
one major framework sits on either side of the split, a server that speaks
only one era is unusable by half the ecosystem.

FastMCP 4 is still beta: `4.0.0b2` and `4.0.0b3` have shipped since, and the
stable line is `3.x`. This training pins `4.0.0b1` so the examples stay
reproducible; the concepts do not change between betas, but the API can.

Officially, the four Tier 1 SDKs (TypeScript, Python, Go, C#) support
2026-07-28, with Rust in beta. Go shipped as `1.7.0-pre.1` and C# behind
`--prerelease` during the beta window.

## Do the two frameworks actually differ in features?

Yes, and it is worth separating the observation from the cause, because the
obvious explanation is the wrong one.

**Microsoft's framework supports MCP sampling. OpenAI's does not.**
`agent_framework/_mcp.py` implements the whole path: a `sampling_callback`, a
`SamplingApprovalCallback` gate, and a declared `types.SamplingCapability`
sent at connect time. `openai-agents` has nothing equivalent anywhere under
`agents/mcp/`.

**But this is not caused by the protocol era.** Two measurements say so:

- `openai-agents` declares **empty** client capabilities in *both* eras. The
  legacy `initialize` sends `"capabilities": {}` and the modern
  `server/discover` sends `"clientCapabilities": {}`. It never advertised
  sampling, so upgrading did not take anything away.
- The modern SDK has not dropped sampling either. `mcp 2.0.0` still ships
  `SamplingCapability`, `SamplingMessage`, `SamplingToolsCapability` and
  friends, consistent with the spec deprecating these for twelve months rather
  than deleting them.

So the difference is a **product decision by each SDK**, not a consequence of
which protocol era it sits on. Microsoft chose to implement sampling; OpenAI
did not. That Microsoft also happens to be on `mcp<2` is a separate fact.

**How much that feature is worth is shrinking, though.** Sampling needs a
server willing to initiate it, and the modern era removed the server-to-client
back-channel that carried it. FastMCP 4 dropped `ctx.sample()` outright. So a
Microsoft agent can offer sampling, but against a FastMCP 4 server there is
nothing on the other end to ask. The capability is real; the set of servers
that can use it is not growing.

**One detail worth stealing regardless of framework.** Microsoft's default is
to *deny* server-initiated sampling unless you supply an approval callback,
and to bound approved requests. Their own comment explains why:

> MCP servers are untrusted third parties, so the default `sampling_callback`
> denies requests unless an approval callback is supplied, and bounds the cost
> of any approved request.

with `_DEFAULT_SAMPLING_MAX_TOKENS = 4096` and
`_DEFAULT_SAMPLING_MAX_REQUESTS = 25` per connection. Sampling inverts the
usual trust direction: a tool server gets to ask *your* model to generate
text, on your budget. Deny-by-default with an explicit gate and a cost ceiling
is the right posture.

## Verified: legacy clients still work

The claim "v2 servers continue to accept legacy 2025-11-25 handshake
requests" is testable, so it was tested. The same three servers were probed
from two isolated environments:

```
MODERN  mcp 2.0.0  + fastmcp 4.0.0b1         -> 3/3 servers OK
MODERN  mcp 2.0.0  + openai-agents 0.21.1    -> 3/3 servers OK
LEGACY  mcp 1.29.0 + openai-agents 0.19.2    -> 3/3 servers OK
```

The middle row is the interesting one: the *same* `openai-agents` package,
two versions apart, once on each side of the protocol split, both reaching
the same unchanged servers.

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

# Pin the version explicitly. A plain `pip install openai-agents` now
# resolves to mcp 2.x, so it would give you a second modern env, not a
# legacy one.
python -m venv /tmp/venv_legacy
/tmp/venv_legacy/bin/pip install "openai-agents==0.19.2"   # holds mcp<2
/tmp/venv_legacy/bin/python 05_compat_check.py             # legacy env
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
- Version data read live from PyPI and the npm registry, 2026-08-18.
