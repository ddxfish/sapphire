# core/chat/display_format.py
"""Pure formatter: canonical chat-history shape → frontend display shape.

Lives in its own module (no FastAPI / route imports) so it can be unit-tested
without spinning up the whole app — and so it has zero coupling to the
specific route file that consumes it. The single producer is the GET
`/api/history` route in `core/routes/chat.py`; the single consumer is the
frontend's `parseContent` in `interfaces/web/static/ui-parsing.js`.
"""
from typing import Any, Dict, List
from core.chat.llm_errors import is_error_row


def format_messages_for_display(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Transform canonical message structure into display format for the UI.

    Canonical shape (what's persisted on disk via `add_*` methods on
    `ConversationHistory`):
      - `{role: "user", content: <str> | <list[block]>}`
      - `{role: "assistant", content: <str>, tool_calls?: [...]}`
      - `{role: "tool", tool_call_id, name, content}`

    Display shape (what the frontend renderer expects):
      - User bubbles emit standalone, with optional `images`/`files`.
      - Assistant turns roll up into a single block with `parts` containing
        `{type: "content"|"tool_call"|"tool_result"}` entries — the frontend
        renders parts in order so a single message bubble can show prose +
        a tool call summary + a tool result accordion.

    Two healing branches are present for legacy / wire-format corruption:
      1. Tool messages missing `name` — handled in `get_messages_for_llm` for
         the LLM call path; this function reads `msg.get("name")` defensively
         too.
      2. Claude-native wire-format tool results stored as `role:"user"` with
         list-content `tool_result` blocks — caused by the heartbeat path
         persisting raw wire format pre-2026-05-11. Treated identically to
         a `role:"tool"` message: the block becomes a `tool_result` part on
         the rolling assistant block, and the user message itself is
         suppressed if it contained ONLY tool_result blocks (otherwise the
         user would see an empty bubble).
    """
    display_messages: List[Dict[str, Any]] = []
    current_block: Dict[str, Any] | None = None

    def finalize_block(block: Dict[str, Any]) -> Dict[str, Any]:
        result = {
            "role": "assistant",
            "parts": block.get("parts", []),
            "timestamp": block.get("timestamp"),
        }
        if block.get("metadata"):
            result["metadata"] = block["metadata"]
        if block.get("persona"):
            result["persona"] = block["persona"]
        if block.get("speaker"):
            result["speaker"] = block["speaker"]
            result["name"] = block.get("name")
        return result

    for msg in messages:
        role = msg.get("role")

        if role == "user":
            content = msg.get("content", "")

            # Heal Claude-native wire-format tool results that may have landed
            # on disk before the wire→canonical writer fix shipped (heartbeat
            # path, 2026-05-11). They look like `role=user` with list-content
            # `tool_result` blocks. Treat each as if it had been a `role=tool`
            # message — push as a `tool_result` part on the rolling assistant
            # block, identical to the `role=tool` branch below. If the user
            # message contains ONLY tool_result blocks (the heartbeat case),
            # don't emit an empty user bubble at all.
            if isinstance(content, list):
                tool_result_blocks = [
                    b for b in content
                    if isinstance(b, dict) and b.get("type") == "tool_result"
                ]
                if tool_result_blocks:
                    for block in tool_result_blocks:
                        if current_block is None:
                            current_block = {
                                "role": "assistant",
                                "parts": [],
                                "timestamp": msg.get("timestamp"),
                            }
                        block_content = block.get("content", "")
                        if isinstance(block_content, list):
                            text_parts = [
                                b.get("text", "") for b in block_content
                                if isinstance(b, dict) and b.get("type") == "text"
                            ]
                            block_content = "\n".join(t for t in text_parts if t)
                        current_block["parts"].append({
                            "type": "tool_result",
                            "name": block.get("name", "tool"),
                            "result": str(block_content),
                            "tool_call_id": block.get("tool_use_id") or block.get("tool_call_id", ""),
                        })
                    content = [
                        b for b in content
                        if not (isinstance(b, dict) and b.get("type") == "tool_result")
                    ]
                    if not content:
                        continue

            if current_block:
                display_messages.append(finalize_block(current_block))
                current_block = None

            user_msg = {
                "role": "user",
                "timestamp": msg.get("timestamp"),
            }
            if msg.get("persona"):
                user_msg["persona"] = msg["persona"]
            if msg.get("speaker"):
                user_msg["speaker"] = msg["speaker"]
                user_msg["name"] = msg.get("name")

            if isinstance(content, list):
                text_parts: List[str] = []
                images: List[Dict[str, Any]] = []
                user_files: List[Dict[str, Any]] = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif block.get("type") == "image":
                            # Stored image (image upgrade 2026-09-10): the row
                            # carries the store handle; the browser fetches it
                            # from /api/tool-image/<id>. Legacy rows: base64.
                            img = {"media_type": block.get("media_type", "image/jpeg")}
                            handle = str(block.get("handle") or "")
                            if handle.startswith("img:"):
                                img["id"] = handle[4:]
                            else:
                                img["data"] = block.get("data", "")
                            images.append(img)
                        elif block.get("type") == "file":
                            user_files.append({
                                "filename": block.get("filename", ""),
                                "text": block.get("text", ""),
                            })
                    elif isinstance(block, str):
                        text_parts.append(block)
                user_msg["content"] = " ".join(text_parts)
                if images:
                    user_msg["images"] = images
                if user_files:
                    user_msg["files"] = user_files
            else:
                user_msg["content"] = content

            display_messages.append(user_msg)

        elif role == "assistant":
            # A room (core/rooms.py, 2026-10-11): a line from a different
            # speaker is a bubble of its own, with its own face and name -
            # the roll-up folded every bot of a round into one bubble.
            if current_block is not None and msg.get("speaker") \
                    and current_block.get("speaker") != msg.get("speaker"):
                display_messages.append(finalize_block(current_block))
                current_block = None
            # A failed turn's row is a bubble of its own: folded in, its
            # metadata took over the block and painted the tool step before
            # it (real results) as the failure.
            if current_block is not None and is_error_row(msg):
                display_messages.append(finalize_block(current_block))
                current_block = None
            if current_block is None:
                current_block = {
                    "role": "assistant",
                    "parts": [],
                    "timestamp": msg.get("timestamp"),
                }
                if msg.get("speaker"):
                    current_block["speaker"] = msg["speaker"]
                    current_block["name"] = msg.get("name")

            # A room line's footnote (core/rooms.py, 2026-10-12): the tools the
            # seat used that turn, before her words like a chat's own tool rows.
            # A local seat's carry inputs and results (they ran here); a guest's
            # are names only (they ran on her box, where the results stay). No
            # tool_call_id: the x button would try to remove a row that is not here.
            for t in msg.get("tools") or []:
                if not isinstance(t, dict) or not t.get("name"):
                    continue
                part = {"type": "tool_result", "name": t["name"], "tool_call_id": None,
                        "result": t["result"] if t.get("result") is not None
                        else "(ran on her machine; the result stays there)"}
                if isinstance(t.get("args"), dict) and t["args"]:
                    part["inputs"] = t["args"]
                current_block["parts"].append(part)

            content = msg.get("content", "")
            if content:
                current_block["parts"].append({
                    "type": "content",
                    "text": content,
                })

            if msg.get("metadata"):
                current_block["metadata"] = msg["metadata"]

            if msg.get("persona") and "persona" not in current_block:
                current_block["persona"] = msg["persona"]

            if msg.get("tool_calls"):
                for tc in msg.get("tool_calls", []):
                    current_block["parts"].append({
                        "type": "tool_call",
                        "id": tc.get("id"),
                        "name": tc.get("function", {}).get("name"),
                        "arguments": tc.get("function", {}).get("arguments"),
                    })

        elif role == "tool":
            if current_block is None:
                current_block = {
                    "role": "assistant",
                    "parts": [],
                    "timestamp": msg.get("timestamp"),
                }

            tool_part = {
                "type": "tool_result",
                "name": msg.get("name"),
                "result": msg.get("content", ""),
                "tool_call_id": msg.get("tool_call_id"),
            }

            if "tool_inputs" in msg:
                tool_part["inputs"] = msg["tool_inputs"]

            current_block["parts"].append(tool_part)

    if current_block:
        display_messages.append(finalize_block(current_block))

    return display_messages
