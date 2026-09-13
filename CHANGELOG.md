# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-09-13

### Added

- Initial public release.
- FastMCP stdio server (`server.py`) exposing seven tools: `list_video_models`,
  `list_image_models`, `generate_image`, `submit_video`, `get_video_status`,
  `download_video`, and `generate_video_and_wait`.
- Async video flow (submit / poll status / download) plus a resumable
  `generate_video_and_wait` that survives client tool timeouts and can resume by
  job id.
- Path sandboxing for caller-supplied paths: server-generated filenames only,
  with absolute paths and `..` rejected.
- Self-locating wrapper (`bin/openrouter-media-mcp`) that reads the API key from
  the macOS Keychain (service `OPENROUTER_MEDIA_KEY`), falling back to a
  gitignored `.env`.
- Per-client registration templates for Claude Code, OpenCode, OMP, and Codex.
- Documentation: setup and usage (`README.md`), model pricing and capability
  reference (`docs/models.md`), and design rationale (`docs/design.md`).
- `SECURITY.md` describing private vulnerability reporting and secret handling.
- MIT license.

[Unreleased]: https://github.com/serral/openrouter-media-mcp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/serral/openrouter-media-mcp/releases/tag/v0.1.0
