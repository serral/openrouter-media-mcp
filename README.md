# openrouter-media-mcp

A local MCP server, logical name `openrouter-media`, that exposes OpenRouter's
image generation, video generation, image-to-video, and model discovery to
local AI clients (Claude Code, OpenCode, OMP, Codex). Everything runs stdio,
local today; a remote transport is possible future work.

## Architecture

```
 Claude Desktop  (not currently registered)

 Claude Code     ----\
 OpenCode        -----> wrapper (bin/openrouter-media-mcp) --> server.py --> OpenRouter API
 OMP             ----/        (loads the key,                 (FastMCP,
 Codex            ---/          execs server)                   stdio)
```

Every client points at the same wrapper executable. The wrapper is the only
place that touches the secret. The server never receives the key from a client
config file.

## Verified API cheat sheet

Source: https://openrouter.ai/docs/guides/overview/multimodal/video-generation

| Purpose | Method and path | Notes |
|---|---|---|
| Submit video | `POST /api/v1/videos` | Fields: `model`, `prompt` (required); optional `duration`, `resolution`, `aspect_ratio`, `size`, `frame_images` (image-to-video), `input_references`, `generate_audio`, `seed`, `callback_url`, `provider`. If both `frame_images` and `input_references` are set, `frame_images` wins. |
| Video status | `GET /api/v1/videos/{id}` | Status: `pending`, `in_progress`, `completed`, `failed`. |
| Video download | `GET /api/v1/videos/{id}/content?index=0` | |
| Video model list | `GET /api/v1/videos/models` | Video models do not appear in the plain `GET /api/v1/models` list. |
| Image, dedicated | `POST /api/v1/images` | Fields: `model`, `prompt`, optional reference images, `aspect_ratio` or `image_size`. Response `data[]` with `b64_json` and `media_type`. |
| Image, via chat | `POST /api/v1/chat/completions` | With `modalities: ["image","text"]`. Images come back in `message.images[].image_url.url` as data URLs. |
| Model discovery | `GET /api/v1/models` | Filter on `output_modalities` containing `"image"` for image models. List at runtime instead of hardcoding IDs. |
| Auth | Header `Authorization: Bearer <OPENROUTER_MEDIA_KEY>` | |

Other notes: polling interval suggested by the docs is about 30 s; generation
takes 30 s to several minutes. Pricing is per video-second with resolution
tiers, see [docs/models.md](docs/models.md) for current rates. Result
retention window is unconfirmed by OpenRouter; download promptly on
`completed`.

## Tool surface

| Tool | Purpose |
|---|---|
| `list_video_models` | Calls `GET /videos/models`. |
| `list_image_models` | Calls `GET /models`, filtered on `output_modalities` containing `image`. |
| `generate_image` | Calls `POST /images`, persists the image into the media dir, returns the file path (optionally as MCP image content). Never returns raw base64 in tool text: one image is megabytes of model context. |
| `submit_video` | Calls `POST /videos`, returns a job id immediately. Optional `first_frame` (image inside the media dir) for image-to-video. Optional `generate_audio`: omit to use the API default (audio-capable models bill at the with-audio rate), or pass `false` to force silent output at the cheaper rate where supported. |
| `get_video_status` | Calls `GET /videos/{id}`, returns status and progress. |
| `download_video` | Calls `GET /videos/{id}/content` (exposes `index`, default 0), writes the MP4 into the media dir, returns the path. |
| `generate_video_and_wait` | Convenience: submit, then poll every 10 to 15 s with backoff. Accepts an optional `job_id` to resume an existing job instead of submitting a new one (resubmission double-bills). Emits MCP progress notifications while polling. Auto-downloads when status reaches `completed` (retention window unconfirmed). Caps the wait below the client's tool-call timeout (default 120 s) and returns the job id if the cap runs out. |

Design rules:
- No single tool call blocks past a client's tool timeout; the
  submit / status / download split keeps every call short.
- No hardcoded default video model. The caller picks from
  `list_video_models`; defaults when omitted are short and cheap (4 s, 720p).
- Path sandboxing: the server computes all output paths from
  `OPENROUTER_MEDIA_DIR`, generates filenames itself (timestamp, model slug,
  short hash), and rejects absolute paths and `..` in any argument.
- Server hygiene: stderr-only logging (stdout is the MCP protocol channel);
  explicit httpx connect/read timeouts on every API call; a `failed` job
  surfaces the API's error text.

## Setup

### Prerequisites

- Python and [`uv`](https://docs.astral.sh/uv/) installed.
- An OpenRouter API key. Create one dedicated to this MCP server, with a
  credit limit set in the dashboard as a cost backstop.

### Install

```bash
git clone <this repo>
cd openrouter-media-mcp
uv sync
```

### Provide the API key

The wrapper (`bin/openrouter-media-mcp`) reads the key itself so no client
config ever holds it:

1. **macOS Keychain (primary):**
   ```bash
   security add-generic-password -s OPENROUTER_MEDIA_KEY -a "$USER" -w "sk-or-..."
   ```
2. **`.env` fallback:** copy `.env.example` to `.env` in the repo root and
   fill in `OPENROUTER_MEDIA_KEY`. `.env` is gitignored. Used only if the
   Keychain item above does not exist.

The wrapper execs the project's own venv interpreter
(`.venv/bin/python server.py`) by absolute path, so it works even under a
GUI client's minimal PATH (no dependency on `uv` or `npx` at runtime).

### Environment variables

| Variable | Purpose |
|---|---|
| `OPENROUTER_MEDIA_KEY` | The OpenRouter API key dedicated to this project. Provided by the wrapper (Keychain primary, `.env` fallback). Never hardcode this in a committed file. |
| `OPENROUTER_MEDIA_DIR` | Where generated images and videos are written. Default: `./media` inside the repo, gitignored. |

### Output path safety

The server only writes inside the configured media dir, rejects absolute
paths and `..` in tool arguments, and generates filenames itself.

## Per-client registration

Server name `openrouter-media`, wrapper path
`/absolute/path/to/openrouter-media-mcp/bin/openrouter-media-mcp` in every
client below. Replace `/absolute/path/to/openrouter-media-mcp` with the real
path to your clone (run `pwd` inside the repo). No env block is needed in any
client config, because the wrapper loads the secret itself and resolves its
own repo root at run time.

Registrations are scoped project-local: Claude Code `--scope local`,
OpenCode project `opencode.json`, OMP project `.omp/mcp.json`, Codex project
`.codex/config.toml`. No user/global config is touched, so no other project
on your machine sees this server.

OpenCode, OMP, and Codex each read a project file that must contain an
absolute path, so those three are generated from committed `.example`
templates rather than committed directly:

```bash
for f in .codex/config.toml .omp/mcp.json .opencode/opencode.json; do
  sed "s#/absolute/path/to/openrouter-media-mcp#$(pwd)#g" "${f}.example" > "$f"
done
```

Run that once after cloning, from the repo root. The generated files contain
no secrets, only your own clone's path, which is why they stay gitignored.

### Claude Code

```bash
claude mcp add --scope local --transport stdio openrouter-media -- /absolute/path/to/openrouter-media-mcp/bin/openrouter-media-mcp
claude mcp list
claude mcp get openrouter-media
```

`local` scope is stored in `~/.claude.json` under the project's path,
visible only in sessions opened in this repo. `/mcp` in a session shows
status and lists the 7 tools. Relevant env vars: `MCP_TIMEOUT` (server
startup, ms), `MCP_TOOL_TIMEOUT` (per tool call, ms).

Note: OMP imports Claude Code's *user-scope* entries only; a local-scope
entry like this one does not propagate to OMP. Configure OMP via its own
project file below.

### OpenCode

Config lives in `.opencode/opencode.json` in the repo (not the documented
root `opencode.json`). Generated from the committed
`.opencode/opencode.json.example` template (see above).

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "openrouter-media": {
      "type": "local",
      "command": ["/absolute/path/to/openrouter-media-mcp/bin/openrouter-media-mcp"],
      "enabled": true,
      "timeout": 180000
    }
  }
}
```

The server name sits directly under `"mcp"`: there is no `"servers"` level.
Optional fields: `cwd`, `environment`, `timeout` (milliseconds; scopes tool
fetching, default 5000 ms).

`opencode mcp list` shows connection status; `opencode mcp add`,
`opencode mcp auth`, `opencode mcp debug <name>` are also available.

### OMP (Oh My Pi)

Path: `.omp/mcp.json` in the repo (project scope, applies under every OMP
profile). The user-scope file `~/.omp/agent/mcp.json` is deliberately not
used. Generated from the committed `.omp/mcp.json.example` template.

```json
{
  "$schema": "https://raw.githubusercontent.com/can1357/oh-my-pi/main/packages/coding-agent/src/config/mcp-schema.json",
  "mcpServers": {
    "openrouter-media": {
      "type": "stdio",
      "command": "/absolute/path/to/openrouter-media-mcp/bin/openrouter-media-mcp",
      "timeout": 180000
    }
  }
}
```

**`timeout` is not optional here.** OMP's default MCP request timeout is
30 s; a 120 s `generate_video_and_wait` call would be killed without this
field. The process-wide `OMP_MCP_TIMEOUT_MS` env var overrides per-server
values.

OMP also imports MCP definitions from other tools' *user-scope* files
(Claude Code, Codex, OpenCode, Cursor, Windsurf, VS Code, Gemini CLI).
Because this rollout registers nothing at user scope, nothing leaks into
OMP through discovery. In-app commands: `/mcp list`, `/mcp add`,
`/mcp reload`, `/mcp test openrouter-media`.

### Codex CLI

Project file `.codex/config.toml`, generated from the committed
`.codex/config.toml.example` template. Do not touch the global
`~/.codex/config.toml`.

```toml
[mcp_servers.openrouter-media]
command = "/absolute/path/to/openrouter-media-mcp/bin/openrouter-media-mcp"
```

```bash
codex mcp add openrouter-media -- /absolute/path/to/openrouter-media-mcp/bin/openrouter-media-mcp
```

### Claude Desktop

Not currently registered: Claude Desktop's config is app-global rather than
project-scoped, so it is out of scope for this project-local rollout. If you
want it anyway, add an `openrouter-media` entry under `mcpServers` in
`~/Library/Application Support/Claude/claude_desktop_config.json` pointing
at the same wrapper path, then restart Claude Desktop.

## Security notes

- **Key handling.** No client config contains the key; every client
  references only the wrapper's path. Only the wrapper touches the secret.
- **Key naming and source.** `OPENROUTER_MEDIA_KEY` is the reserved name for
  the OpenRouter API key dedicated to this project. Primary source: macOS
  Keychain via `security find-generic-password` (no install needed).
  Fallback: the gitignored `.env` in the repo root, only if Keychain is
  unavailable. Shell-only exports (e.g. `.zshrc`) are not sufficient for GUI
  apps such as Claude Desktop, which do not inherit shell environment.
- **Dedicated key.** Create one OpenRouter API key for this MCP with a
  credit limit set in the dashboard as the cost backstop.
- **Residual exposure.** The wrapper exports the key into the server
  process environment, where the same user can read it via `ps -E`.
  Acceptable for a single-user machine; if that assumption changes, move the
  key read inside the server, per call.
- **Never commit secrets.** `.gitignore` covers `.env`, `media/`, and
  `.venv/` from the first commit.
- **Path sandboxing.** See "Output path safety" above.

## Pricing

Video is billed per video-second, with resolution tiers, and can range from
cents to several dollars per clip depending on model, duration, and
resolution. Defaults are short and cheap (4 s, 720p); opt into a specific
model and duration for anything larger. Set a credit limit on the dedicated
key in the OpenRouter dashboard as a backstop. See
[docs/models.md](docs/models.md) for current per-model rates.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Claude Desktop can't find the key | GUI apps do not inherit `.zshrc`. The wrapper loads the key itself; check the Keychain item exists: `security find-generic-password -s OPENROUTER_MEDIA_KEY -a "$USER" -w`. |
| Server fails to start only from a GUI client | A PATH-dependent binary inside the wrapper (`uv`, `npx`). The wrapper must exec `.venv/bin/python` by absolute path. |
| One-time Keychain prompt on first GUI use | Expected Keychain ACL behavior. Choose "Always Allow" once, re-test. |
| An OMP tool call dies after ~30 s | OMP's default MCP request timeout. Set `"timeout": 180000` on the server entry (or `OMP_MCP_TIMEOUT_MS`). |
| A video tool call times out client-side | Long single-call polling. Use the `submit_video` / `get_video_status` / `download_video` split, or `generate_video_and_wait` with its 120 s cap. |
| A video model does not show up in model lists | Video models are not in `GET /api/v1/models`. Use `GET /api/v1/videos/models`. |
| `download_video` 404s long after completion | OpenRouter's result retention window is unconfirmed. Download promptly; `generate_video_and_wait` auto-downloads on `completed`. |
| OpenCode config seems to be ignored | Wrong nesting: the server name goes directly under `"mcp"`, with no `"servers"` level. |
| Codex does not pick up the config | Confirm the project `.codex/config.toml` exists and Codex is installed and on PATH; the global `~/.codex/config.toml` is deliberately left alone. |

## Documentation

- [docs/models.md](docs/models.md): current image and video model list and
  pricing.
- [docs/design.md](docs/design.md): design notes and rationale behind the
  architecture and client-registration approach.
