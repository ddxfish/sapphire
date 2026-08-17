import json
import logging
import time
import re
import uuid
from typing import Dict, Any, Optional, List

from .llm_providers import LLMResponse
from .llm_providers.base import BaseProvider

logger = logging.getLogger(__name__)


def filter_to_thinking_only(content: str) -> str:
    """
    Extract only <think> tags, removing all other content.
    Used for assistant messages with tool_calls to prevent premature responses.
    """
    if not content:
        return ""
    
    think_pattern = r'<(?:seed:)?think[^>]*>.*?</(?:seed:)?think[^>]*>'
    think_matches = re.findall(think_pattern, content, re.DOTALL | re.IGNORECASE)
    
    filtered = "\n\n".join(think_matches) if think_matches else ""
    
    # If no think tags but there's content, wrap FULL content (don't truncate!)
    if not filtered and content:
        filtered = f"<think>{content.strip()}</think>"
        logger.info(f"No think tags found, wrapped full content in think block")
    
    if filtered != content:
        logger.info(f"Stripped prose: {len(content)} -> {len(filtered)} chars")
    
    return filtered

def strip_ui_markers(content: str) -> str:
    """
    Strip UI-only markers from content before sending to LLM.
    
    Removes patterns like:
    - <<IMG::image_id>>
    - <<FILE::file_id>>
    - Any other <<MARKER::data>> patterns
    
    These markers are kept in history for UI parsing but removed from LLM context.
    """
    if not content:
        return content
    
    # Pattern to match <<TYPE::data>> markers
    marker_pattern = r'<<[A-Z]+::[^>]+>>\s*'
    
    # Remove all markers
    clean = re.sub(marker_pattern, '', content)
    
    # Log if we stripped anything
    if clean != content:
        markers_found = re.findall(marker_pattern, content)
        logger.info(f"[CLEANUP] Stripped {len(markers_found)} UI markers from tool result for LLM context")
        for marker in markers_found:
            logger.debug(f"   - {marker.strip()}")
    
    return clean.strip()


def wrap_tool_result(tool_call_id: str, function_name: str, result: str) -> Dict[str, Any]:
    """
    Wrap tool results in standard OpenAI tool format.
    
    Args:
        tool_call_id: The tool call ID
        function_name: Name of the function that was called
        result: The result string from the function (ALREADY STRIPPED of UI markers)
    
    Returns:
        Properly formatted message dict for the LLM
    """
    clean_result = strip_ui_markers(result) if '<<' in result else result
    
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "name": function_name,
        "content": clean_result
    }


def wire_to_canonical(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert a wire-format message list to canonical persistence shape.

    Some providers (Claude, Anthropic-compat) emit tool results as
    `{role:"user", content:[{type:"tool_result", tool_use_id, content}]}`
    so the next API call carries them in the shape the model expects. The
    canonical persistence form, used by `format_messages_for_display` and
    every OpenAI-style reader, is `{role:"tool", tool_call_id, name, content}`.

    The foreground chat path avoids this mismatch by writing canonical
    directly through `history.add_tool_result()` during execution. The
    heartbeat / ExecutionContext path doesn't have a history object, so its
    `messages` slice arrives at persistence time still in wire form. Without
    converting, every Claude-backed scheduled task ends up with
    invisible-bubble corruption in its named chat.

    This walks the input and:
      - Splits any `role=user` list-content `tool_result` blocks into
        separate canonical `role=tool` messages (one per block) via the
        existing `wrap_tool_result` shape, looking up `name` from the most
        recent preceding assistant `tool_calls` within the slice.
      - Preserves any non-`tool_result` blocks (text/image/file) on a
        stripped user message at the original position. In practice
        `format_tool_result` only emits a single `tool_result` block per
        message — the mixed branch is defensive.
      - Passes everything else through unchanged. Image-injection user
        messages from `_inject_tool_images` (text + image blocks) are
        already a shape the display formatter handles, so they pass.

    Pure function — does not mutate the input list.
    """
    result: List[Dict[str, Any]] = []
    name_by_tool_use_id: Dict[str, str] = {}

    for msg in messages:
        role = msg.get("role")

        # Build the rolling name lookup from assistant tool_calls so we can
        # populate canonical `name` on the converted tool messages below.
        if role == "assistant":
            tcs = msg.get("tool_calls")
            if isinstance(tcs, list):
                for tc in tcs:
                    if not isinstance(tc, dict):
                        continue
                    tc_id = tc.get("id")
                    fn_name = (tc.get("function") or {}).get("name", "tool")
                    if tc_id:
                        name_by_tool_use_id[tc_id] = fn_name
            result.append(msg)
            continue

        if role == "user" and isinstance(msg.get("content"), list):
            content = msg["content"]
            tool_result_blocks = [
                b for b in content
                if isinstance(b, dict) and b.get("type") == "tool_result"
            ]
            if tool_result_blocks:
                for block in tool_result_blocks:
                    tc_id = block.get("tool_use_id") or block.get("tool_call_id", "")
                    name = name_by_tool_use_id.get(tc_id, "tool")
                    block_content = block.get("content", "")
                    # tool_result content can itself be a list of typed blocks
                    # (Claude allows nested text/image inside a tool_result);
                    # flatten the text parts to a single string for canonical
                    # storage. Image blocks inside tool results aren't a path
                    # we exercise today — fall through to str() if needed.
                    if isinstance(block_content, list):
                        text_parts = [
                            b.get("text", "") for b in block_content
                            if isinstance(b, dict) and b.get("type") == "text"
                        ]
                        block_content = "\n".join(t for t in text_parts if t)
                    result.append(wrap_tool_result(tc_id, name, str(block_content)))

                # Preserve any non-tool_result blocks at the original position
                # so a defensive mixed message doesn't lose its text/image data.
                non_tool_blocks = [
                    b for b in content
                    if not (isinstance(b, dict) and b.get("type") == "tool_result")
                ]
                if non_tool_blocks:
                    result.append({**msg, "content": non_tool_blocks})
                continue

        result.append(msg)

    return result


def cap_tool_result_text(text, function_name=None):
    """Head-preserving cap on tool-result text (TOOL_RESULT_MAX_CHARS, 0=off).

    HEAD-preserving is mandatory: <<IMG::tool:id>> markers are PREPENDED to
    the text and are the ONLY liveness reference protecting saved image bytes
    from the orphan-GC sweep (history.py orphan cleanup) — a tail-keeping cap
    would silently delete the user's images.
    """
    try:
        import config
        limit = int(getattr(config, 'TOOL_RESULT_MAX_CHARS', 200_000))
    except Exception:
        limit = 200_000
    if limit <= 0 or len(text) <= limit:
        return text
    dropped = len(text) - limit
    who = f" from '{function_name}'" if function_name else ""
    logger.warning(f"[TOOL] result{who} capped: {len(text)} -> {limit} chars")
    return (text[:limit] +
            f"\n[... {dropped} chars truncated — this result{who} exceeded "
            f"Sapphire's {limit}-char tool-result cap. This is a size limit, "
            f"NOT a tool failure. Re-run with a narrower scope (smaller range, "
            f"fewer items, a filter) if you need the rest. Any images this "
            f"tool returned were unaffected.]")


def _extract_tool_images(result, history=None, provider=None, function_name=None):
    """Extract images from a tool result if it returned structured data.

    Tools can return {"text": "...", "images": [{"data": base64, "media_type": "image/..."}]}
    to pass images back to the LLM on the next turn.

    Images are saved to the chat history DB and <<IMG::tool:id>> markers are embedded
    in the text so they persist in history and render via existing image infrastructure.

    If the provider doesn't support vision, images are still saved (UI + history
    still render them) but the LLM-bound images list is empty, and the tool result
    text gets a "vision unavailable" suffix. This keeps the conversation a clean
    tool-call → tool-result → assistant-response pair instead of injecting a fake
    user message after the tool result. 2026-05-15.

    Returns (text_str, images_list). images_list contains the raw image dicts
    for injection into the next LLM turn.
    """
    if isinstance(result, dict) and "images" in result and isinstance(result["images"], list):
        text = str(result.get("text", ""))
        images = [
            img for img in result["images"]
            if isinstance(img, dict) and img.get("data")
        ]
        supports_vision = bool(provider and getattr(provider, 'supports_images', False))
        logger.info(f"[VISION-DEBUG] tool returned {len(result['images'])} image(s); "
                    f"provider={type(provider).__name__ if provider else None} "
                    f"supports_vision={supports_vision} → {'LLM sees them' if supports_vision else 'CLIP fallback'}")
        # Save images to DB and embed markers in text
        # Images with display_only=True are saved for user gallery but not sent to LLM
        llm_images = []
        hidden_to_vibe = []  # hidden from the LLM, but described via CLIP so it still gets the gist
        for img in images:
            img_id = _save_tool_image(img, history)
            if img_id:
                text = f"<<IMG::tool:{img_id}>>\n{text}"
            if not img.get("display_only") and supports_vision:
                llm_images.append(img)
            elif img.get("vibe_when_hidden") or (not supports_vision and not img.get("display_only")):
                # CLIP describe fires only as a FALLBACK: the model was meant to
                # see this image (not display_only) but the provider has no
                # vision — or the tool explicitly opted in (vibe_when_hidden).
                # display_only images are deliberately hidden; describing them
                # defeats the point, and CLIP's subject-blind guesses read as
                # "wrong image" to literal models → regeneration loops. 2026-08-09.
                hidden_to_vibe.append(img)
        if hidden_to_vibe:
            # Generate a CLIP-based atmospheric description so the model still
            # gets the "vibe" of what was captured/produced.
            vibe_parts = []
            for img in hidden_to_vibe:
                try:
                    import base64 as _b64
                    from core import vibes as _vibes
                    raw = _b64.b64decode(img["data"])
                    vibe_parts.append(_vibes.describe(raw))
                except Exception as e:
                    logger.warning(f"[VIBES] failed to describe image: {e}")
            if vibe_parts:
                text = (text + "\n\n" + "\n\n".join(vibe_parts)).strip()
            elif not supports_vision:
                text = (text + "\n\n[Note: current model does not support image inputs - "
                        f"image(s) were captured and saved but cannot be analyzed this turn.]").strip()
        # Cap AFTER marker-prepend so head-keeping preserves every marker.
        return cap_tool_result_text(text, function_name), llm_images
    return cap_tool_result_text(str(result), function_name), []


def _save_tool_image(img, history=None):
    """Save a base64 tool image to the chat history database. Returns image ID or None."""
    import base64

    try:
        img_id = uuid.uuid4().hex[:12]
        media_type = img.get("media_type", "image/jpeg")
        ext = "png" if "png" in media_type else "jpg"
        full_id = f"{img_id}.{ext}"

        img_bytes = base64.b64decode(img["data"])

        if history and hasattr(history, 'save_tool_image'):
            # EFFECTIVE chat, not active: a phone/background stream's image
            # belongs to ITS chat — keying it to the operator's open chat
            # both mis-cascades AND stores a private chat's image plaintext
            # (the encrypt decision reads the owner's vaulted flag). P3-T4.
            owner = None
            if hasattr(history, '_effective_chat_name'):
                owner = history._effective_chat_name()
            history.save_tool_image(full_id, img_bytes, media_type, chat_name=owner)
            logger.info(f"[TOOL] Saved tool image to DB: {full_id}")
        else:
            # Fallback to disk if no history available (isolated tool calls)
            from pathlib import Path
            img_dir = Path(__file__).parent.parent.parent / "user" / "tool_images"
            img_dir.mkdir(parents=True, exist_ok=True)
            (img_dir / full_id).write_bytes(img_bytes)
            logger.info(f"[TOOL] Saved tool image to disk (no history): {full_id}")

        return full_id
    except Exception as e:
        logger.error(f"[TOOL] Failed to save tool image: {e}")
        return None


class ToolCallingEngine:
    def __init__(self, function_manager):
        self.function_manager = function_manager

    def call_llm_with_metrics(self, provider: BaseProvider, messages: List, gen_params: Dict, tools: List = None) -> LLMResponse:
        """Call LLM with performance metrics via provider abstraction."""
        logger.info(f"[TOOL] LLM CALL [{provider.provider_name}]: {len(messages)} messages, {sum(len(str(m)) for m in messages)} chars")
        
        start_time = time.time()
        
        response = provider.chat_completion(messages, tools=tools, generation_params=gen_params)
        
        elapsed = time.time() - start_time
        
        # Log performance + diagnostic trinity (finish_reason, reasoning tokens).
        # Without these, "short response + fast t/s" is ambiguous — could be
        # the model legitimately stopping, could be hitting max_tokens, could
        # be reasoning tokens eating budget invisibly. 2026-04-24.
        try:
            usage = response.usage or {}
            finish = getattr(response, 'finish_reason', None) or '?'
            reasoning_tok = usage.get('reasoning_tokens', 0)
            content_chars = len(str(response.content)) if response.content else 0
            if usage.get('completion_tokens'):
                tps = usage['completion_tokens'] / elapsed
                logger.info(
                    f"LLM ({provider.model}): {elapsed:.2f}s, {content_chars} chars, "
                    f"{tps:.1f} t/s, finish={finish}, reasoning_tok={reasoning_tok}"
                )
            else:
                logger.info(
                    f"LLM ({provider.model}): {elapsed:.2f}s, {content_chars} chars, "
                    f"finish={finish}"
                )
        except (AttributeError, ZeroDivisionError, TypeError):
            pass
        
        return response

    def extract_function_call_from_text(self, text: str) -> Optional[Dict]:
        """Extract function calls from text (LM Studio, Qwen3, GLM, etc compatibility)."""
        if not text:
            return None

        # Format 1: {"function_call": {"name": "...", "arguments": {...}}}
        pattern = r'(\{"function_call":\s*\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})'
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)

        if match:
            try:
                parsed = json.loads(match.group(1))
                if "function_call" in parsed and "name" in parsed["function_call"]:
                    return parsed
            except json.JSONDecodeError as e:
                logger.debug(f"JSON parse failed for format 1: {e}")

        # Format 2: <function_call>{"name": "...", "arguments": {...}}</function_call> (Qwen3, etc)
        xml_pattern = r'<function_call>\s*(\{.*?\})\s*</function_call>'
        xml_match = re.search(xml_pattern, text, re.IGNORECASE | re.DOTALL)

        if xml_match:
            try:
                inner = json.loads(xml_match.group(1))
                if "name" in inner:
                    return {"function_call": inner}
            except json.JSONDecodeError as e:
                logger.debug(f"JSON parse failed for XML format: {e}")

        # Format 3: <tool_call>{"name": "...", "arguments": {...}}</tool_call> (GLM, etc)
        tool_call_pattern = r'<tool_call>\s*(\{.*?\})\s*</tool_call>'
        tool_call_match = re.search(tool_call_pattern, text, re.IGNORECASE | re.DOTALL)

        if tool_call_match:
            try:
                inner = json.loads(tool_call_match.group(1))
                if "name" in inner:
                    return {"function_call": inner}
            except json.JSONDecodeError as e:
                logger.debug(f"JSON parse failed for tool_call format: {e}")

        # Format 4: Raw JSON with name field (fallback) - {"name": "tool_name", "arguments": {...}}
        # Only match if it looks like a standalone tool call (not embedded in prose)
        raw_pattern = r'^\s*(\{"name":\s*"[^"]+",\s*"arguments":\s*\{[^{}]*\}\s*\})\s*$'
        raw_match = re.search(raw_pattern, text, re.MULTILINE)

        if raw_match:
            try:
                inner = json.loads(raw_match.group(1))
                if "name" in inner:
                    return {"function_call": inner}
            except json.JSONDecodeError as e:
                logger.debug(f"JSON parse failed for raw format: {e}")

        # Format 5: nested-XML pseudo tool call (Llama / llama.cpp / functionary
        # style) — NO JSON payload; args are <parameter> tags:
        #   <tool_call><function=NAME><parameter=KEY>VALUE</parameter>...</function></tool_call>
        # The <tool_call> wrapper is optional (some models emit a bare
        # <function=...>). Require the </function> close so we never fire on a
        # partial stream. Lowest precedence — only reached when formats 1-4 miss,
        # so it can't override a working parse. Added 2026-06-20 after a Discord
        # user's local model leaked this dialect as raw text (web_search).
        fn_match = re.search(
            r'<function\s*=\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*>(.*?)</function\s*>',
            text, re.IGNORECASE | re.DOTALL,
        )
        if fn_match:
            args = {}
            for pname, pval in re.findall(
                r'<parameter\s*=\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*>(.*?)</parameter\s*>',
                fn_match.group(2), re.IGNORECASE | re.DOTALL,
            ):
                args[pname] = pval.strip()
            logger.info(f"[TOOL] Parsed nested-XML tool call: {fn_match.group(1)}")
            return {"function_call": {"name": fn_match.group(1), "arguments": args}}

        return None

    def format_tool_calls_for_conversation(self, tool_calls):
        """Convert tool_calls to proper format."""
        tool_calls_formatted = []
        for tool_call in tool_calls:
            tool_calls_formatted.append({
                "id": tool_call.id,
                "type": "function",
                "function": {
                    "name": tool_call.function.name,
                    "arguments": tool_call.function.arguments
                }
            })
        return tool_calls_formatted

    def execute_tool_calls(self, tool_calls, messages, history, provider: BaseProvider = None, scopes=None, allowed_tools=None, executor_snapshot=None, loop_counts=None):
        """
        Execute tool calls and add results to messages array AND history.

        Key behaviors:
        - Tool results sent to LLM have UI markers STRIPPED (clean context)
        - Tool results saved to history contain raw content (structured JSON handles display)
        - Reset function results are NOT saved to history
        - Uses provider.format_tool_result() if provider given (for Claude compatibility)
        - Tools returning {"text": "...", "images": [...]} have images accumulated

        Returns (tools_executed, tool_images) where tool_images is a list of
        {"data": base64, "media_type": "image/..."} dicts from tool results.

        Note: Caller should slice tool_calls to MAX_PARALLEL_TOOLS before calling.
        """
        tools_executed = 0
        tool_images = []

        for tool_call in tool_calls:
            function_name = tool_call["function"]["name"]
            # Loop guard: count every attempt this turn (success, throw, or bad JSON).
            self.function_manager.bump_loop_count(loop_counts, function_name)

            try:
                function_args = json.loads(tool_call["function"]["arguments"])
            except json.JSONDecodeError:
                logger.error(f"Failed to parse tool arguments: {tool_call['function']['arguments']}")
                error_result = "Error: Invalid JSON arguments."
                # Loop-warn goes to the LLM message only — history keeps the raw error
                # (else the warning persists and replays every future turn).
                llm_result = error_result + self.function_manager.loop_warn_suffix(function_name, loop_counts)

                if provider:
                    wrapped_msg = provider.format_tool_result(tool_call["id"], function_name, llm_result)
                else:
                    wrapped_msg = wrap_tool_result(tool_call["id"], function_name, llm_result)
                messages.append(wrapped_msg)

                if history:
                    history.add_tool_result(tool_call["id"], function_name, error_result)
                continue

            try:
                function_result = self.function_manager.execute_function(function_name, function_args, scopes=scopes, allowed_tools=allowed_tools, executor_snapshot=executor_snapshot)
            except Exception as tool_error:
                logger.error(f"Tool execution failed for {function_name}: {tool_error}", exc_info=True)
                function_result = f"Tool '{function_name}' failed: {str(tool_error)}"

            # Extract images if tool returned structured result
            result_str, images = _extract_tool_images(function_result, history, provider, function_name)
            if images:
                tool_images.extend(images)
                logger.info(f"[TOOL] {function_name} returned {len(images)} image(s)")

            clean_result = strip_ui_markers(result_str)
            clean_result += self.function_manager.loop_warn_suffix(function_name, loop_counts)

            if provider:
                wrapped_msg = provider.format_tool_result(tool_call["id"], function_name, clean_result)
            else:
                wrapped_msg = wrap_tool_result(tool_call["id"], function_name, clean_result)
            messages.append(wrapped_msg)

            logger.info(f"[OK] Tool result added to messages")
            logger.debug(f"   Message role: {wrapped_msg['role']}")
            logger.debug(f"   Content preview: {str(wrapped_msg.get('content', ''))[:100]}")

            if history:
                logger.info(f"[SAVE] Saving tool result for: {function_name}")
                history.add_tool_result(tool_call["id"], function_name, result_str, inputs=function_args)
            else:
                logger.debug(f"[ISOLATED] No history manager, skipping save for: {function_name}")

            tools_executed += 1
            logger.info(f"[OK] Executed tool: {function_name}")

        return tools_executed, tool_images

    def execute_text_based_tool_call(self, function_call_data, filtered_content, messages, history, provider: BaseProvider = None, scopes=None, allowed_tools=None, executor_snapshot=None, loop_counts=None):
        """
        Execute text-based function call (LM Studio compatibility).
        
        Args:
            function_call_data: The parsed function call data
            filtered_content: Pre-filtered content (thinking only) from caller
            messages: Messages array to append to
            history: History manager to save to
            provider: Optional provider for format_tool_result (Claude compatibility)
        
        Returns tool call ID.
        """
        tool_call_id = f"call_{uuid.uuid4().hex[:8]}"
        function_name = function_call_data["function_call"]["name"]
        function_args = function_call_data["function_call"]["arguments"]
        # Loop guard: count this attempt toward the per-turn total.
        self.function_manager.bump_loop_count(loop_counts, function_name)

        # Some text-based-tool-call providers deliver `arguments` as an already-
        # JSON-encoded string; others as a dict. json.dumps on a string would
        # double-encode (writes `"\"{...}\""` to history), bricking the chat
        # for strict providers on the next turn. Honor whatever the LLM gave us.
        if isinstance(function_args, str):
            args_json = function_args
        else:
            args_json = json.dumps(function_args)

        tool_calls_formatted = [{
            "id": tool_call_id,
            "type": "function",
            "function": {
                "name": function_name,
                "arguments": args_json
            }
        }]
        
        messages.append({
            "role": "assistant",
            "content": filtered_content,
            "tool_calls": tool_calls_formatted
        })
        
        if history:
            history.add_assistant_with_tool_calls(filtered_content, tool_calls_formatted)

        # Executors expect a dict, but text formats can deliver `arguments` as
        # a JSON-encoded string (common from local models). Unparsed, every
        # tool on this path dies with "'str' object has no attribute 'get'"
        # and the model retry-spirals. History above keeps the raw string.
        if isinstance(function_args, str):
            try:
                parsed = json.loads(function_args)
                function_args = parsed if isinstance(parsed, dict) else {}
            except Exception:
                logger.warning(f"[TOOL] Unparseable string arguments for {function_name}; executing with empty args")
                function_args = {}

        try:
            function_result = self.function_manager.execute_function(function_name, function_args, scopes=scopes, allowed_tools=allowed_tools, executor_snapshot=executor_snapshot)
        except Exception as tool_error:
            logger.error(f"Text-based tool failed for {function_name}: {tool_error}")
            function_result = f"Tool '{function_name}' failed: {str(tool_error)}"

        result_str, tool_images = _extract_tool_images(function_result, history, provider, function_name)
        if tool_images:
            logger.info(f"[TOOL] {function_name} returned {len(tool_images)} image(s) (text-based)")
        clean_result = strip_ui_markers(result_str)
        clean_result += self.function_manager.loop_warn_suffix(function_name, loop_counts)

        if provider:
            wrapped_msg = provider.format_tool_result(tool_call_id, function_name, clean_result)
        else:
            wrapped_msg = wrap_tool_result(tool_call_id, function_name, clean_result)
        messages.append(wrapped_msg)

        if history:
            history.add_tool_result(tool_call_id, function_name, result_str, inputs=function_args)
        else:
            logger.debug(f"[ISOLATED] No history manager, skipping save for: {function_name}")

        logger.info(f"[OK] Executed text-based tool: {function_name}")
        return tool_call_id, tool_images