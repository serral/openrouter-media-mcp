# Design

This document explains the reasoning behind openrouter-media-mcp's
architecture: why it exists as a custom server, how secrets and file output
are handled, and why the video tools are shaped the way they are.

## Architecture overview

The server sits between AI coding assistants and OpenRouter's media
generation API:

```
client (Claude Code, OpenCode, OMP, Codex, ...)
  -> self-locating wrapper script
    -> server.py (FastMCP, stdio transport)
      -> OpenRouter API
```

Each client spawns the wrapper script as a subprocess and speaks MCP over
stdio. The wrapper resolves the repository's own location at run time, sets
up the API key and output directory as environment variables, and execs the
project's Python interpreter directly (bypassing tools like `uv` that may
not be on a GUI application's minimal `PATH`). `server.py` then handles the
actual tool calls, translating them into HTTP requests against OpenRouter's
verified media endpoints. Nothing above the wrapper ever sees the API key,
and nothing below `server.py` ever touches the client's process directly.

Because the wrapper is self-locating, the whole project can be renamed or
moved without hand-editing paths inside it or breaking any client
registration that points at it.

## Why a custom server instead of an existing package

There is an existing npm package that already speaks MCP for OpenRouter
media generation. It was evaluated as an alternative and rejected in favor
of a purpose-built Python server on top of FastMCP, for a few reasons:

- **Tool surface control.** The existing package exposes around nineteen
  tools spanning far more of OpenRouter's API than this project needs.
  Every tool a client sees adds to the context an LLM has to reason over
  before it makes a single call; a server with exactly the tools this
  workflow needs (image generation, video generation, and model discovery)
  keeps that overhead down.
- **Review depth.** A server this project owns means every line that
  touches the API key or writes a file to disk is something we wrote and
  can audit directly, rather than trusting an external maintainer's
  release process.
- **Security history.** The reference package has a published
  path-traversal advisory in its history, the general bug class where a
  file-writing tool accepts caller-supplied paths that can escape the
  intended output directory. That history was treated as a concrete reason
  to design path handling defensively from the start rather than as an
  afterthought (see "Path sandboxing" below).
- **Transport headroom.** FastMCP has a native Streamable HTTP transport
  with pluggable auth providers, which matters if this server ever needs to
  be reachable from more than one machine (see "Future" below). The
  reference package is stdio-only outside third-party hosting.

The reference package remains useful purely as prior art: it is one of the
few open implementations that has exercised OpenRouter's image generation
endpoint, so its request and response handling was worth reading while
building this server's own HTTP calls.

## Secret handling

The OpenRouter API key never appears in a client configuration file, a
command line, or a committed file. Instead:

- The wrapper reads the key from the macOS Keychain first, using a
  dedicated Keychain entry that exists only for this project's key.
- If Keychain access is unavailable, the wrapper falls back to a local
  `.env` file, which is excluded from version control.
- The wrapper is the only piece of code that reads the secret from either
  source; it exports it into the environment for the server process it
  spawns, and every client configuration only ever points at the wrapper
  script, never at the key itself.

This keeps the key out of the places that tend to leak: shell history,
committed config files, and version control diffs. The accepted trade-off
is that the key is briefly visible in the wrapper's process environment to
other processes running as the same local user, which is considered
acceptable on a single-user machine.

## Why video generation is split into separate steps

Video generation on OpenRouter is asynchronous: a job is submitted, takes
time to render, and is retrieved once complete. Rather than one tool call
that blocks until the video is ready, this is exposed as three primitives,
`submit_video`, `get_video_status`, and `download_video`, plus a convenience
tool, `generate_video_and_wait`, that wraps them.

Two constraints drove this design:

- **Client timeouts.** MCP clients enforce their own timeout on a single
  tool call, and some default well under a minute. A tool that blocks for
  the full duration of a video render risks the client giving up and
  reporting failure even though the job is still processing on
  OpenRouter's side. Splitting submission, polling, and retrieval into
  separate calls means no single call has to outlive a render.
- **Context budget.** Returning a finished video as inline base64 data
  would put megabytes of encoded bytes into the model's context window for
  a single tool result. Instead, generated media is written to disk and
  the tool returns a file path.

`generate_video_and_wait` exists as a convenience layer on top of the three
primitives: it submits a job, polls with backoff while emitting progress
notifications so the client UI shows liveness, and downloads automatically
once the job completes. It caps its own wait below a typical client
timeout and, if the cap is reached before the job finishes, returns the job
identifier instead of failing. The same tool accepts that identifier on a
later call to resume polling an existing job rather than submitting a new
one, since resubmitting would create and bill for a second render.

## Path sandboxing

Every tool that writes a file computes its output path entirely inside the
server, rooted at a configured media directory. The server generates the
filename itself (a timestamp, a slug derived from the model name, and a
short hash) rather than accepting a caller-supplied filename outright. Any
path-like input that is absolute or contains a `..` segment is rejected
before it is used. This closes off the path-traversal bug class referenced
above: a caller cannot direct output outside the intended media directory,
whether by supplying an absolute path or by walking upward with relative
segments.

## No hardcoded default video model

`submit_video` and `generate_video_and_wait` require the caller to supply a
model id, and that id is expected to come from calling `list_video_models`
first. There is deliberately no hardcoded fallback model. OpenRouter's
video catalog changes over time: prices, durations, and available features
differ substantially between models, and video is billed per second, so a
stale hardcoded default risks silently generating an expensive or
unavailable result. Forcing model discovery through `list_video_models`
keeps model choice current with whatever OpenRouter actually offers at call
time. Duration and resolution do have modest, low-cost defaults, since
those are safe to default without hiding a cost decision from the caller.
See [models.md](models.md) for current model pricing.

## Future: remote transport

Today the server only supports the stdio transport, which means it can
only be reached by clients running on the same machine that spawns the
wrapper. A natural next step, if this server needs to be reachable from
elsewhere, is FastMCP's HTTP transport, paired with an authentication token
and bound so it is only reachable from within a private network rather than
the public internet. That work is not implemented yet; this document notes
it as a direction rather than a plan, since the concrete choice of network
and authentication mechanism depends on where and how the server would
actually need to be reached.
