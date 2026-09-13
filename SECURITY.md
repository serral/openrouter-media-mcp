# Security Policy

## Reporting a vulnerability

Please report security issues privately through GitHub's private vulnerability
reporting: open the repository's **Security** tab and choose
**Report a vulnerability**. This keeps the report confidential until a fix is
available.

Do not open a public issue for a suspected vulnerability, and do not include
secrets (API keys, tokens) in a report.

Please include enough detail to reproduce: affected file or tool, steps, and
the impact you observed. A response can be expected within a reasonable time,
and coordinated disclosure is appreciated once a fix is ready.

## Handling of secrets

This server never stores the OpenRouter API key in the repository or in any
client configuration. The key is read at runtime by `bin/openrouter-media-mcp`
from the macOS Keychain (service name `OPENROUTER_MEDIA_KEY`), falling back to a
gitignored `.env` file in the repository root. `.env*` is ignored by git, with a
single committed `.env.example` that contains a placeholder only.

Use a dedicated OpenRouter key for this server with a spending limit set, so a
compromise is bounded and easy to revoke.

## Scope

This is a local stdio MCP server intended to run on the same machine as its
clients. There is no network listener today. Any future remote transport should
be placed behind authentication and a private network before exposure.
