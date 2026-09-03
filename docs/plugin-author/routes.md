# HTTP Routes

Plugins can register custom HTTP endpoints. Auth, CSRF, and rate limiting are enforced by the framework — your handler code never touches any of that.

## Manifest Declaration

```json
{
  "capabilities": {
    "routes": [
      {
        "method": "POST",
        "path": "capture/{request_id}",
        "handler": "routes/capture.py:handle_capture"
      }
    ]
  }
}
```

The full URL becomes: `POST /api/plugin/{plugin_name}/{path}`

For the example above: `POST /api/plugin/webcam/capture/abc123`

## Route Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `method` | string | No | `GET`, `POST`, `PUT`, or `DELETE` (default: `GET`) |
| `path` | string | Yes | URL path (supports `{param}` placeholders) |
| `handler` | string | Yes | `file:function` reference (default function: `handle`) |

## Handler Signature

The framework calls your handler with **keyword arguments**: every `{name}` path param, plus `body`, `settings`, `credentials`, `query`, and `request`. Always end your signature with `**_` to absorb the ones you don't use — otherwise the call raises `TypeError` on the first request (the framework always passes all of them).

```python
def handle_capture(request_id, body=None, settings=None, **_) -> dict:
    """
    Injected keyword args (declare only what you need, then **_):
        request_id:  path param extracted from {request_id}
        body:        parsed JSON body (POST/PUT/DELETE — yes, DELETE bodies
                     are parsed; empty dict for GET, multipart, or non-object JSON)
        settings:    plugin settings from user/webui/plugins/{name}.json
        credentials: the credentials manager (resolve secrets server-side)
        query:       dict of URL query-string params
        request:     the raw Starlette Request object

    Returns:
        dict (JSON, HTTP 200), a (dict, status_code) tuple, or a
        FastAPI/Starlette Response object
    """
    return {"status": "ok"}
```

Path parameters arrive as keyword arguments matching the `{name}` in your path pattern. Because `body`, `settings`, `credentials`, `query`, and `request` are **always** passed, your handler must accept them by name or swallow them with `**_`.

## Return Values

A handler can return any of:

- **`dict`** — serialized to JSON with HTTP 200
- **`(dict, status_code)` tuple** — serialized to JSON with that status code. This is how you return errors: `return {"error": "no such item"}, 404`. The tuple only unpacks when it has exactly two elements and the second is an `int`
- **A FastAPI/Starlette `Response` object** — passed through untouched (files, custom headers, streaming)

Without the tuple convention a `(dict, 404)` return would serialize as a JSON *array* with HTTP 200 — the framework unpacks it so error statuses actually reach the client.

## Security

All of the following are enforced automatically — you cannot disable them:

- **Authentication**: `require_login` dependency — session or API key required
- **CSRF**: Middleware validates tokens on POST/PUT/DELETE from browser sessions
- **Rate limiting**: 60 GET / 30 non-GET requests per minute, per plugin (bucketed by session — or by bearer-token hash when bearer auth is used)
- **Bearer-token auth (opt-in)**: a plugin may *add* (never weaken) a bearer path by writing `user/plugin_state/{plugin}_mcp_key.json` (`{"key": "..."}`). A request whose `Authorization: Bearer <key>` matches then bypasses session login — used by MCP clients. Session CSRF still cannot be disabled.

## Example: Webcam Capture Endpoint

```
plugins/webcam/
  plugin.json
  routes/capture.py
  tools/webcam.py
```

**plugin.json:**
```json
{
  "name": "webcam",
  "version": "1.0.0",
  "capabilities": {
    "tools": ["tools/webcam.py"],
    "routes": [
      {
        "method": "POST",
        "path": "capture/{request_id}",
        "handler": "routes/capture.py:handle_capture"
      }
    ]
  }
}
```

**routes/capture.py:**
```python
import threading

# Pending capture requests: {request_id: {"event": Event, "image": None}}
_pending = {}
_lock = threading.Lock()

def create_request(request_id, timeout=15):
    """Called by the tool — blocks until browser POSTs the image."""
    event = threading.Event()
    with _lock:
        _pending[request_id] = {"event": event, "image": None}
    event.wait(timeout=timeout)
    with _lock:
        data = _pending.pop(request_id, {})
    return data.get("image")

def handle_capture(request_id: str, body: dict, **_) -> dict:
    """Called by the browser — delivers the captured image."""
    with _lock:
        req = _pending.get(request_id)
    if not req:
        return {"error": "No pending request"}
    req["image"] = body
    req["event"].set()
    return {"status": "ok"}
```

## Notes

- Request bodies are parsed for POST, PUT, **and DELETE** (multipart is left for the handler to read from `request` directly; a non-object JSON body arrives as `{}`)
- Routes are registered on plugin load and removed on unload
- Hot reload (`POST /api/plugins/{name}/reload`) re-registers routes
- Handlers can be sync or async — async handlers are awaited directly, sync handlers run in a threadpool
- Path parameters only match single path segments (no slashes)

## Reference for AI

- Declare: `capabilities.routes` = `[{method: GET|POST|PUT|DELETE (default GET), path (supports {param}, single-segment match only), handler: "file.py:function" (default function: handle)}]`. Mounted at `/api/plugin/{plugin_name}/{path}`.
- Handler kwargs ALWAYS passed: every path param + `body` + `settings` + `credentials` + `query` + `request` — end the signature with `**_` or the first request raises TypeError.
- `body`: parsed JSON dict for POST/PUT/DELETE (DELETE bodies ARE parsed — confirm tokens ride them); `{}` for GET, multipart requests, unparseable JSON, or a non-object JSON body.
- Returns: `dict` → JSON 200; `(dict, int)` 2-tuple → JSON with that status code; `Response` object → passed through; anything else → FastAPI default serialization.
- Sync handlers run in a threadpool; async handlers are awaited directly.
- Enforced, not disableable: session auth (`require_login`), CSRF on POST/PUT/DELETE from browser sessions, rate limit 60 GET / 30 non-GET per minute per plugin.
- Bearer opt-in: `user/plugin_state/{plugin}_mcp_key.json` = `{"key": "..."}` lets a matching `Authorization: Bearer` header bypass session login (rate-bucketed by token hash). Additive only — session CSRF can't be weakened.
- Routes register on load, drop on unload, re-register on hot reload.
