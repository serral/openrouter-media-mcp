"""openrouter-media: FastMCP stdio server exposing OpenRouter media generation.

Tools map 1:1 onto the endpoints in README.md "Verified API cheat sheet":
POST /videos, GET /videos/{id}, GET /videos/{id}/content?index=N,
GET /videos/models, POST /images, GET /models (image-capable filtered
client-side). Response shapes beyond the cheat sheet are UNVERIFIED, so every
parser here is tolerant (top-level id / job_id / data.id; content may be raw
bytes or JSON containing a URL).

Env:
  OPENROUTER_MEDIA_KEY  canonical API key, read at call time (missing key does
                        not prevent startup; tools return a clear error)
  OPENROUTER_MEDIA_DIR  output dir, default <repo>/media, created on demand
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import time
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import httpx
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError

log = logging.getLogger("openrouter-media")

API_BASE = "https://openrouter.ai/api/v1"
# Explicit connect/read (plus write/pool) on every call: never rely on httpx defaults.
HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)
POLL_START_S = 10.0
POLL_MAX_S = 15.0
DEFAULT_MAX_WAIT_S = 120


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _api_key() -> str:
    key = (os.environ.get("OPENROUTER_MEDIA_KEY") or "").strip()
    if not key:
        raise ToolError(
            "OPENROUTER_MEDIA_KEY is not set. Provide it via the environment "
            "(the bin/openrouter-media-mcp wrapper supplies it from Keychain "
            "or .env). OPENROUTER_API_KEY is not used by this server."
        )
    return key


def _media_dir() -> Path:
    raw = (os.environ.get("OPENROUTER_MEDIA_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    # default: <repo>/media (repo = parent dir of server.py)
    return Path(__file__).resolve().parent / "media"


def _reject_unsafe_path(value: str) -> str:
    """Validate a caller-supplied path-like string: no absolute, no '..'."""
    if not isinstance(value, str) or not value.strip():
        raise ToolError("Path-like argument must be a non-empty string.")
    v = value.strip()
    if PurePosixPath(v).is_absolute() or PureWindowsPath(v).is_absolute() or os.path.isabs(v):
        raise ToolError(f"Rejected path argument {value!r}: absolute paths are not allowed.")
    if ".." in PurePosixPath(v).parts or ".." in PureWindowsPath(v).parts or v == "..":
        raise ToolError(f"Rejected path argument {value!r}: '..' is not allowed.")
    return v


def _slug(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", text.strip()).strip("-._")
    if not slug:
        raise ToolError(f"Cannot derive a filename component from {text!r}.")
    return slug[:60]


def _output_path(slug: str, ext: str, requested: str | None = None) -> Path:
    """Sandboxed output path inside the media dir.

    Filenames are server-generated (timestamp + slug + short hash). If the
    caller supplies a path-like hint, only its basename is honored and it must
    pass traversal validation (GHSA-3q7p-736f-x44v bug class).
    """
    if requested is not None:
        _reject_unsafe_path(requested)
        name = Path(requested.replace("\\", "/")).name
        if not name or name in {".", ".."}:
            raise ToolError(f"Rejected path argument {requested!r}: no usable basename.")
        name = _slug(Path(name).stem) + ext
    else:
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        short = hashlib.sha1(f"{slug}|{time.time_ns()}".encode()).hexdigest()[:8]
        name = f"{stamp}_{slug}_{short}{ext}"
    media = _media_dir()
    path = (media / name).resolve()
    if media not in path.parents and path != media:
        raise ToolError(f"Refusing to write outside the media dir: {path}")
    return path


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=API_BASE,
        headers={
            "Authorization": f"Bearer {_api_key()}",
            "Content-Type": "application/json",
        },
        timeout=HTTP_TIMEOUT,
    )


def _api_error_text(resp: httpx.Response) -> str:
    """Best-effort extraction of the API's error message."""
    try:
        body = resp.json()
    except Exception:
        return f"HTTP {resp.status_code}: {resp.text[:500]}"
    err = body.get("error") if isinstance(body, dict) else body
    if isinstance(err, dict):
        err = err.get("message") or err.get("error") or json.dumps(err)
    if isinstance(body, dict) and body.get("message") and not err:
        err = body["message"]
    text = str(err) if err is not None else ""
    return f"HTTP {resp.status_code}: {text or resp.text[:500]}"


def _raise_api(resp: httpx.Response, what: str) -> None:
    raise ToolError(f"{what} failed: {_api_error_text(resp)}")


def _extract_job_id(payload: Any) -> str:
    """Tolerant: top-level id / job_id / data.id."""
    if isinstance(payload, dict):
        for key in ("id", "job_id", "jobId"):
            if isinstance(payload.get(key), str) and payload[key]:
                return payload[key]
        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("id", "job_id", "jobId"):
                if isinstance(data.get(key), str) and data[key]:
                    return data[key]
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict):
                for key in ("id", "job_id", "jobId"):
                    if isinstance(first.get(key), str) and first[key]:
                        return first[key]
    raise ToolError(f"Could not find a job id in the submit response: {json.dumps(payload)[:500]}")


def _extract_status(payload: Any) -> tuple[str, dict[str, Any]]:
    """Tolerant status extraction; returns (status, extra_fields)."""
    node = payload if isinstance(payload, dict) else {}
    data = node.get("data") if isinstance(node.get("data"), dict) else node
    status = ""
    for key in ("status", "state"):
        val = data.get(key) if isinstance(data, dict) else None
        if isinstance(val, str) and val:
            status = val.lower()
            break
    extras = {k: v for k, v in (data.items() if isinstance(data, dict) else []) if k not in ("status", "state")}
    return status, extras


def _extract_error_text(extras: dict[str, Any]) -> str:
    for key in ("error", "failure_reason", "failure_message"):
        val = extras.get(key)
        if isinstance(val, str) and val:
            return val
        if isinstance(val, dict):
            msg = val.get("message")
            if msg:
                return str(msg)
    return json.dumps(extras)[:500] if extras else "unknown error"


def _json_or_bytes(resp: httpx.Response) -> tuple[bytes | None, dict[str, Any] | None]:
    """Media endpoints may return raw bytes or JSON containing a URL."""
    ctype = resp.headers.get("content-type", "")
    if ctype.split(";")[0].strip().startswith("application/json"):
        try:
            return None, resp.json()
        except Exception:
            return None, None
    if resp.content:
        return resp.content, None
    return None, None


def _first_url(payload: Any) -> str | None:
    """Depth-limited search for a URL string inside an arbitrary JSON shape."""
    if isinstance(payload, str) and payload.startswith(("http://", "https://")):
        return payload
    if isinstance(payload, dict):
        for key in ("url", "video_url", "content_url", "download_url", "file_url"):
            val = payload.get(key)
            if isinstance(val, str) and val.startswith(("http://", "https://")):
                return val
        for val in payload.values():
            url = _first_url(val)
            if url:
                return url
    if isinstance(payload, list):
        for val in payload:
            url = _first_url(val)
            if url:
                return url
    return None


def _media_type_ext(media_type: str | None) -> str:
    mt = (media_type or "").split(";")[0].strip().lower()
    mapping = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "video/mp4": ".mp4",
        "video/webm": ".webm",
    }
    if mt in mapping:
        return mapping[mt]
    if mt.startswith("image/"):
        return "." + mt.split("/", 1)[1].replace("jpeg", "jpg")
    if mt.startswith("video/"):
        return "." + mt.split("/", 1)[1]
    return ".png"


def _download_url(url: str) -> bytes:
    with httpx.Client(timeout=HTTP_TIMEOUT) as client:
        resp = client.get(url)
        if resp.status_code >= 400:
            raise ToolError(f"Downloading media from {url} failed: {_api_error_text(resp)}")
        return resp.content



def _read_frame_data_url(source: str) -> str:
    """Return a data: URL for an image inside the media dir (read-only input
    for image-to-video). The resolved file must live inside
    OPENROUTER_MEDIA_DIR; no other location may be read."""
    if not source or not source.strip():
        raise ToolError("first_frame source path is empty.")
    media_dir = _media_dir().resolve()
    candidate = Path(source).expanduser().resolve()
    if candidate != media_dir and media_dir not in candidate.parents:
        raise ToolError(
            f"first_frame must reference a file inside the media dir "
            f"({media_dir}); got: {source}"
        )
    if not candidate.is_file():
        raise ToolError(f"first_frame image not found: {candidate}")
    import base64
    import mimetypes

    mime = mimetypes.guess_type(candidate.name)[0] or "image/png"
    encoded = base64.b64encode(candidate.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"

# --------------------------------------------------------------------------- #
# server + tools
# --------------------------------------------------------------------------- #

mcp = FastMCP("openrouter-media")


@mcp.tool
def list_video_models() -> list[dict[str, Any]]:
    """List video generation models from OpenRouter (GET /videos/models).

    Video models do not appear in the plain models list, so use this to pick
    a model id for submit_video / generate_video_and_wait.
    """
    with _client() as client:
        resp = client.get("/videos/models")
        if resp.status_code >= 400:
            _raise_api(resp, "list_video_models")
        payload = resp.json()
    data = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), list) else payload
    if not isinstance(data, list):
        raise ToolError(f"Unexpected /videos/models response shape: {json.dumps(payload)[:500]}")
    return [
        {"id": m.get("id"), "name": m.get("name"), **{k: v for k, v in m.items() if k not in ("id", "name")}}
        for m in data
        if isinstance(m, dict) and m.get("id")
    ]


@mcp.tool
def list_image_models() -> list[dict[str, Any]]:
    """List image-capable models (GET /models, filtered client-side on
    output_modalities containing "image")."""
    with _client() as client:
        resp = client.get("/models")
        if resp.status_code >= 400:
            _raise_api(resp, "list_image_models")
        payload = resp.json()
    data = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), list) else payload
    if not isinstance(data, list):
        raise ToolError(f"Unexpected /models response shape: {json.dumps(payload)[:500]}")
    out: list[dict[str, Any]] = []
    for m in data:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        arch = m.get("architecture")
        modalities = m.get("output_modalities") or (
            arch.get("output_modalities") if isinstance(arch, dict) else None
        )
        if isinstance(modalities, list) and "image" in [str(x).lower() for x in modalities]:
            out.append({"id": m.get("id"), "name": m.get("name"), "output_modalities": modalities})
    return out


@mcp.tool
def generate_image(
    model: str,
    prompt: str,
    aspect_ratio: str | None = None,
    image_size: str | None = None,
) -> dict[str, Any]:
    """Generate image(s) via POST /images and persist them to the media dir.

    Returns the written file path(s); never returns base64 in tool text.
    Pass aspect_ratio (e.g. "1:1") or image_size (e.g. "1024x1024"), not both.
    """
    if not model or not prompt:
        raise ToolError("Both model and prompt are required.")
    body: dict[str, Any] = {"model": model, "prompt": prompt}
    if aspect_ratio:
        body["aspect_ratio"] = aspect_ratio
    if image_size:
        body["image_size"] = image_size

    with _client() as client:
        resp = client.post("/images", json=body)
        if resp.status_code >= 400:
            _raise_api(resp, "generate_image")
        payload = resp.json()

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise ToolError(f"Unexpected /images response shape: {json.dumps(payload)[:500]}")

    written: list[str] = []
    media_dir = _media_dir()
    media_dir.mkdir(parents=True, exist_ok=True)
    slug = _slug(model)
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        ext = _media_type_ext(item.get("media_type"))
        b64 = item.get("b64_json")
        url = item.get("url") if isinstance(item.get("url"), str) else None
        if b64:
            import base64

            raw = base64.b64decode(b64)
        elif url:
            raw = _download_url(url)
        else:
            log.warning("image item %d had neither b64_json nor url; skipping", i)
            continue
        path = _output_path(slug, ext)
        path.write_bytes(raw)
        written.append(str(path))
        log.info("wrote %s (%d bytes)", path, len(raw))

    if not written:
        raise ToolError(f"Image generation returned no usable image data: {json.dumps(payload)[:500]}")
    return {"paths": written, "media_dir": str(media_dir)}


@mcp.tool
def submit_video(
    model: str,
    prompt: str,
    duration: int = 4,
    resolution: str = "720p",
    first_frame: str | None = None,
    generate_audio: bool | None = None,
) -> dict[str, Any]:
    """Submit a video generation job (POST /videos) and return immediately.

    Requires a model id from list_video_models (no hardcoded default).
    Defaults: 4 s, 720p. Returns the job id for get_video_status /
    download_video / generate_video_and_wait(resume via job_id).

    generate_audio: None (default) omits the field from the request body so
    the API default applies (audio-capable models bill at the with-audio
    rate); False includes generate_audio: false to force silent output and
    bill the cheaper without-audio rate where the model supports it; True
    includes generate_audio: true.
    """
    if not model or not prompt:
        raise ToolError("Both model and prompt are required.")
    body = {"model": model, "prompt": prompt, "duration": duration, "resolution": resolution}
    if first_frame:
        body["frame_images"] = [
            {
                "type": "image_url",
                "image_url": {"url": _read_frame_data_url(first_frame)},
                "frame_type": "first_frame",
            }
        ]
    if generate_audio is not None:
        body["generate_audio"] = generate_audio
    with _client() as client:
        resp = client.post("/videos", json=body)
        if resp.status_code >= 400:
            _raise_api(resp, "submit_video")
        payload = resp.json()
    job_id = _extract_job_id(payload)
    status, _ = _extract_status(payload)
    log.info("submitted video job %s (%s)", job_id, model)
    return {"job_id": job_id, "status": status or "submitted", "model": model}


@mcp.tool
def get_video_status(job_id: str) -> dict[str, Any]:
    """Fetch a video job's status (GET /videos/{id}).

    Status is one of pending, in_progress, completed, failed. A failed job
    surfaces the API's error text.
    """
    if not job_id:
        raise ToolError("job_id is required.")
    with _client() as client:
        resp = client.get(f"/videos/{job_id}")
        if resp.status_code >= 400:
            _raise_api(resp, "get_video_status")
        payload = resp.json()
    status, extras = _extract_status(payload)
    out: dict[str, Any] = {"job_id": job_id, "status": status or "unknown"}
    if status == "failed":
        out["error"] = _extract_error_text(extras)
    for key in ("progress", "eta", "created_at", "completed_at"):
        if key in extras:
            out[key] = extras[key]
    return out


@mcp.tool
def download_video(job_id: str, index: int = 0) -> dict[str, Any]:
    """Download a completed video (GET /videos/{id}/content?index=N) and write
    the file into the media dir. Returns the file path. index selects among
    multiple outputs (default 0)."""
    if not job_id:
        raise ToolError("job_id is required.")
    with _client() as client:
        resp = client.get(f"/videos/{job_id}/content", params={"index": index})
        if resp.status_code >= 400:
            _raise_api(resp, "download_video")
        raw, payload = _json_or_bytes(resp)
        if raw is None and payload is not None:
            url = _first_url(payload)
            if url:
                raw = _download_url(url)

    if raw is None:
        raise ToolError(
            f"No downloadable content for job {job_id} index {index}: "
            + (json.dumps(payload)[:500] if payload else "empty response")
        )

    media_dir = _media_dir()
    media_dir.mkdir(parents=True, exist_ok=True)
    slug = _slug(f"video-{job_id}-i{index}")
    path = _output_path(slug, ".mp4")
    path.write_bytes(raw)
    log.info("wrote %s (%d bytes)", path, len(raw))
    return {"path": str(path), "bytes": len(raw), "job_id": job_id, "index": index}


@mcp.tool
async def generate_video_and_wait(
    ctx: Context,
    model: str | None = None,
    prompt: str | None = None,
    job_id: str | None = None,
    duration: int = 4,
    resolution: str = "720p",
    generate_audio: bool | None = None,
    max_wait_s: int = DEFAULT_MAX_WAIT_S,
) -> dict[str, Any]:
    """Convenience: submit a video job and poll until it completes (or the
    wait cap runs out, default 120 s - always below client tool-call timeouts).

    Pass job_id to RESUME polling an existing submission instead of submitting
    a new one (resubmission double-bills). Emits MCP progress notifications
    while polling. Auto-downloads the result when the job completes (OpenRouter
    result retention window is unconfirmed, so do not leave finished jobs
    behind). Returns the job id and status if the cap runs out - call again
    with that job_id to resume.

    generate_audio forwards to submit_video on new submissions (None omits the
    field; False bills the cheaper without-audio rate where the model supports
    it); ignored when resuming via job_id.
    """
    # Progress notifications: FastMCP Context is injected by type.
    report_progress = None
    if ctx is not None and hasattr(ctx, "report_progress"):
        report_progress = ctx.report_progress

    if job_id:
        if model or prompt or generate_audio is not None:
            log.info(
                "job_id given; ignoring model/prompt/generate_audio, resuming job %s",
                job_id,
            )
        active_id = job_id
    else:
        if not model or not prompt:
            raise ToolError(
                "model and prompt are required when submitting a new job "
                "(no hardcoded default model); pass job_id alone to resume."
            )
        submit = submit_video(
            model=model,
            prompt=prompt,
            duration=duration,
            resolution=resolution,
            generate_audio=generate_audio,
        )
        active_id = submit["job_id"]

    started = time.monotonic()
    interval = POLL_START_S
    status = ""
    while True:
        elapsed = time.monotonic() - started
        if report_progress is not None:
            try:
                await report_progress(progress=round(elapsed, 1), total=float(max_wait_s))
            except Exception as exc:  # progress is best-effort liveness
                log.debug("report_progress failed: %s", exc)

        status, extras = _extract_status(_status_payload(active_id))
        if status == "completed":
            dl = download_video(job_id=active_id, index=0)
            return {"job_id": active_id, "status": "completed", "path": dl["path"], "bytes": dl["bytes"]}
        if status == "failed":
            raise ToolError(f"Video job {active_id} failed: {_extract_error_text(extras)}")

        if elapsed + interval > max_wait_s:
            log.info("wait cap reached for job %s (status=%s)", active_id, status)
            return {
                "job_id": active_id,
                "status": status or "unknown",
                "waited_s": round(elapsed, 1),
                "note": "wait cap reached; call generate_video_and_wait again with this job_id to resume polling",
            }

        await _sleep(min(interval, max(0.0, max_wait_s - elapsed)))
        interval = min(interval + 1.0, POLL_MAX_S)


async def _sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


def _status_payload(job_id: str) -> Any:
    """Synchronous status fetch shared by the wait tool (kept separate so
    get_video_status stays the user-facing variant)."""
    with _client() as client:
        resp = client.get(f"/videos/{job_id}")
        if resp.status_code >= 400:
            _raise_api(resp, "get_video_status")
        return resp.json()


def main() -> None:
    logging.basicConfig(
        stream=sys.stderr,  # stdout is the MCP protocol channel
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    log.info("openrouter-media MCP server starting (media dir: %s)", _media_dir())
    mcp.run()  # stdio transport by default


if __name__ == "__main__":
    main()
