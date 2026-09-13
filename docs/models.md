# Model Reference

Pricing and capability reference for the video and image models exposed
through this MCP server's OpenRouter backend.

Rates are billed per second of output duration unless noted otherwise, and
some models charge a different rate depending on whether audio generation is
requested.

## Video models

| Model | Resolution | Price | Duration limits | Notes |
| --- | --- | --- | --- | --- |
| `google/veo-3.1-lite` | 720p | $0.03/s without audio, $0.05/s with audio | 4, 6, 8 s | Supports `first_frame` and `last_frame`. Requests that omit `generate_audio: false` are billed at the with-audio rate by default. |
| `black-forest-labs/flux-video-edit` | n/a | $0.03/s | n/a | Edit-only model: takes a source video plus an edit prompt, not a generator. |
| `alibaba/wan-3.0` | 720p: $0.10/s, 480p: $0.05/s | 2-30 s | Supports `first_frame`, audio, and seed. |
| `x-ai/grok-imagine-video` | 480p: $0.05/s, 720p: $0.07/s | - | - |
| `bytedance/seedance-2.0` | 480p-4K | Token-based pricing | - | Supports first and last frame, and audio. |
| `openai/sora-2-pro` | 720p | $0.30/s | - | - |
| `runway/aleph-2` | - | $0.28/s | - | $0.56 minimum charge per generation. |
| `black-forest-labs/flux-3-video` | 720p | $0.17/s | - | - |

## Audio toggle savings

For `google/veo-3.1-lite`, passing `generate_audio: false` bills the
without-audio rate ($0.03/s) instead of the with-audio rate ($0.05/s), a
saving of roughly 40% for a silent clip. For example, a 4-second 720p clip
costs about $0.12 without audio versus $0.20 with audio. If `generate_audio`
is omitted, the API defaults to the with-audio rate.

## Image models

Image model pricing is not itemized here; use `list_image_models` to fetch
the current catalog and per-model rates at runtime.

## Discovering current pricing

Model availability and pricing can change at any time. Use the
`list_video_models` and `list_image_models` tools to fetch the live,
authoritative catalog before making cost-sensitive decisions - do not rely
solely on this document for up-to-date figures.

**Caveat:** the rates in this document were verified 2026-09-13 and are
subject to change. Treat `list_video_models` (and `list_image_models`) as
the source of truth.
