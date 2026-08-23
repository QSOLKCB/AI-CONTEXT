#!/usr/bin/env python3
"""Provider-format parsers for AI-CONTEXT Phase 3.

Provider exports are unstable inputs. Parsers return normalized message observations plus
an explicit layout id, adapter id/version, warnings, and parse status. This module does
not write workspace state.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any

ADAPTER_PROTOCOL_VERSION = "0.1.0"


class AdapterError(RuntimeError):
    pass


@dataclass(frozen=True)
class ParsedMessage:
    text: str
    actor: str | None
    conversation_id: str
    source_local_id: str
    timestamp: str | None = None
    title: str = ""
    kind: str = "conversation_message"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParseResult:
    source_type: str
    adapter_id: str
    adapter_version: str
    layout_id: str
    parse_status: str
    messages: list[ParsedMessage]
    warnings: list[str]


def _status(warnings: list[str], *, force_partial: bool = False) -> str:
    return "partial" if warnings or force_partial else "exact"


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def parse_chatgpt(data: Any) -> ParseResult:
    if not isinstance(data, list) or not data:
        raise AdapterError("ChatGPT adapter expected a non-empty conversations list")
    messages: list[ParsedMessage] = []
    warnings: list[str] = []
    recognized = 0
    for conv_index, conv in enumerate(data):
        if not isinstance(conv, dict) or not isinstance(conv.get("mapping"), dict):
            continue
        recognized += 1
        conv_id = str(conv.get("id") or f"conversation-{conv_index}")
        title = str(conv.get("title") or "")
        before = len(messages)
        nodes = []
        for node_id, node in conv["mapping"].items():
            if not isinstance(node, dict) or not isinstance(node.get("message"), dict):
                continue
            message = node["message"]
            nodes.append((message.get("create_time") is None, message.get("create_time") or 0, str(node_id), message))
        for _, _, node_id, message in sorted(nodes, key=lambda item: (item[0], item[1], item[2])):
            author = message.get("author") if isinstance(message.get("author"), dict) else {}
            actor = author.get("role") if isinstance(author.get("role"), str) else None
            content = message.get("content") if isinstance(message.get("content"), dict) else {}
            parts = content.get("parts") if isinstance(content.get("parts"), list) else []
            text = "\n".join(filter(None, (_stringify(part).strip() for part in parts))).strip()
            if not text:
                continue
            messages.append(ParsedMessage(
                text=text,
                actor=actor,
                conversation_id=conv_id,
                source_local_id=str(message.get("id") or node_id),
                timestamp=str(message.get("create_time")) if message.get("create_time") is not None else None,
                title=title,
                metadata={"provider_layout": "chatgpt-conversations-mapping-v1"},
            ))
        if len(messages) == before:
            warnings.append(f"recognized ChatGPT conversation had no parseable messages: {conv_id}")
    if recognized == 0:
        raise AdapterError("ChatGPT layout not recognized")
    if recognized != len(data):
        warnings.append("some top-level entries did not match the ChatGPT conversation layout")
    return ParseResult(
        source_type="chatgpt-export",
        adapter_id="chatgpt",
        adapter_version=ADAPTER_PROTOCOL_VERSION,
        layout_id="chatgpt-conversations-mapping-v1",
        parse_status=_status(warnings),
        messages=messages,
        warnings=warnings,
    )


def parse_claude(data: Any) -> ParseResult:
    if not isinstance(data, list) or not data:
        raise AdapterError("Claude adapter expected a non-empty conversations list")
    messages: list[ParsedMessage] = []
    warnings: list[str] = []
    recognized = 0
    for conv_index, conv in enumerate(data):
        if not isinstance(conv, dict) or not isinstance(conv.get("chat_messages"), list):
            continue
        recognized += 1
        conv_id = str(conv.get("uuid") or conv.get("id") or f"conversation-{conv_index}")
        title = str(conv.get("name") or conv.get("title") or "")
        before = len(messages)
        for msg_index, message in enumerate(conv["chat_messages"]):
            if not isinstance(message, dict):
                continue
            raw = message.get("text")
            text = "\n".join(_stringify(part) for part in raw).strip() if isinstance(raw, list) else _stringify(raw).strip()
            if not text:
                continue
            messages.append(ParsedMessage(
                text=text,
                actor=str(message.get("sender")) if message.get("sender") is not None else None,
                conversation_id=conv_id,
                source_local_id=str(message.get("uuid") or message.get("id") or f"{conv_id}:{msg_index}"),
                timestamp=str(message.get("created_at")) if message.get("created_at") is not None else None,
                title=title,
                metadata={"provider_layout": "claude-chat-messages-v1"},
            ))
        if len(messages) == before:
            warnings.append(f"recognized Claude conversation had no parseable messages: {conv_id}")
    if recognized == 0:
        raise AdapterError("Claude layout not recognized")
    if recognized != len(data):
        warnings.append("some top-level entries did not match the Claude conversation layout")
    return ParseResult(
        source_type="claude-export",
        adapter_id="claude",
        adapter_version=ADAPTER_PROTOCOL_VERSION,
        layout_id="claude-chat-messages-v1",
        parse_status=_status(warnings),
        messages=messages,
        warnings=warnings,
    )


class _PlainHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())


def _html_text(value: str) -> str:
    parser = _PlainHTML()
    parser.feed(value)
    return " ".join(parser.parts).strip()


def _conversation_id_from_gemini_url(value: Any, fallback: str) -> str:
    if isinstance(value, str):
        match = re.search(r"/app/c/([^/?#]+)", value)
        if match:
            return match.group(1)
    return fallback


def _nested_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return ""
        if stripped[:1] in "[{\"":
            try:
                return _nested_text(json.loads(stripped))
            except (json.JSONDecodeError, TypeError):
                pass
        return stripped
    if isinstance(value, list):
        return "\n".join(filter(None, (_nested_text(item) for item in value))).strip()
    if isinstance(value, dict):
        for key in ("text", "message", "content", "value", "query", "response"):
            if key in value:
                text = _nested_text(value[key])
                if text:
                    return text
        return "\n".join(filter(None, (_nested_text(item) for item in value.values()))).strip()
    return str(value)


def parse_gemini_takeout(data: Any) -> ParseResult:
    if not isinstance(data, list) or not data:
        raise AdapterError("Gemini Takeout adapter expected a non-empty MyActivity JSON array")
    messages: list[ParsedMessage] = []
    warnings: list[str] = [
        "Gemini Takeout is an activity export, not a documented stable conversation schema; response content may be omitted or truncated"
    ]
    recognized = 0
    for event_index, event in enumerate(data):
        if not isinstance(event, dict):
            continue
        products = event.get("products") if isinstance(event.get("products"), list) else []
        looks_gemini = (
            any(str(item).casefold() == "gemini apps" for item in products)
            or str(event.get("header", "")).casefold() == "gemini"
            or "gemini.google.com" in str(event.get("titleUrl", ""))
            or str(event.get("title", "")).startswith("Prompted ")
        )
        if not looks_gemini:
            continue
        recognized += 1
        conv_id = _conversation_id_from_gemini_url(event.get("titleUrl"), f"activity-{event_index}")
        timestamp = str(event.get("time")) if event.get("time") is not None else None
        event_messages: list[tuple[str, str, str]] = []
        layout = "gemini-myactivity-unknown"

        details = event.get("details")
        if isinstance(details, list):
            layout = "gemini-myactivity-details-v1"
            for detail_index, detail in enumerate(details):
                if not isinstance(detail, dict):
                    continue
                name = str(detail.get("name", "")).casefold()
                text = _nested_text(detail.get("value"))
                if not text:
                    continue
                if name == "request":
                    event_messages.append(("user", text, f"details:{detail_index}"))
                elif name == "response":
                    event_messages.append(("assistant", text, f"details:{detail_index}"))

        interactions = event.get("userInteractions")
        if isinstance(interactions, list):
            layout = "gemini-myactivity-userinteractions-v1"
            for interaction_index, wrapper in enumerate(interactions):
                if not isinstance(wrapper, dict):
                    continue
                interaction = wrapper.get("userInteraction")
                if not isinstance(interaction, dict):
                    continue
                request = _nested_text(interaction.get("request"))
                response = _nested_text(interaction.get("response"))
                if request:
                    event_messages.append(("user", request, f"interaction:{interaction_index}:request"))
                if response:
                    event_messages.append(("assistant", response, f"interaction:{interaction_index}:response"))

        if not event_messages and isinstance(event.get("safeHtmlItem"), list):
            layout = "gemini-myactivity-safehtml-v1"
            title = str(event.get("title", ""))
            if title.startswith("Prompted ") and title[9:].strip():
                event_messages.append(("user", title[9:].strip(), "title:prompt"))
            response_parts = []
            for html_index, item in enumerate(event["safeHtmlItem"]):
                if isinstance(item, dict) and isinstance(item.get("html"), str):
                    text = _html_text(item["html"])
                    if text:
                        response_parts.append((html_index, text))
            if response_parts:
                event_messages.append(("assistant", "\n".join(text for _, text in response_parts), "safeHtmlItem:response"))

        if not event_messages:
            warnings.append(f"Gemini activity entry had no recognized message payload: {event_index}")
            continue
        first_user = next((text for role, text, _ in event_messages if role == "user"), "")
        title = first_user[:120] if first_user else ""
        for seq, (role, text, local) in enumerate(event_messages):
            messages.append(ParsedMessage(
                text=text,
                actor=role,
                conversation_id=conv_id,
                source_local_id=f"{conv_id}:{event_index}:{local}:{seq}",
                timestamp=timestamp,
                title=title,
                metadata={"provider_layout": layout, "activity_index": event_index},
            ))
    if recognized == 0:
        raise AdapterError("Gemini Takeout activity layout not recognized")
    if recognized != len(data):
        warnings.append("some MyActivity entries were not Gemini Apps activity and were ignored")
    return ParseResult(
        source_type="gemini-takeout-export",
        adapter_id="gemini",
        adapter_version=ADAPTER_PROTOCOL_VERSION,
        layout_id="gemini-myactivity-mixed-v1",
        parse_status="partial",
        messages=messages,
        warnings=warnings,
    )


def _grok_timestamp(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    try:
        number = value["$date"]["$numberLong"]
        instant = datetime.fromtimestamp(int(number) / 1000, tz=timezone.utc)
        return instant.isoformat().replace("+00:00", "Z")
    except (TypeError, KeyError, ValueError, OverflowError):
        return None


def parse_grok_export(data: Any) -> ParseResult:
    if not isinstance(data, dict) or not isinstance(data.get("conversations"), list):
        raise AdapterError("Grok adapter expected prod-grok-backend.json with conversations[]")
    messages: list[ParsedMessage] = []
    warnings: list[str] = []
    recognized = 0
    omitted_private_reasoning = False
    for conv_index, wrapper in enumerate(data["conversations"]):
        if not isinstance(wrapper, dict) or not isinstance(wrapper.get("conversation"), dict) or not isinstance(wrapper.get("responses"), list):
            warnings.append(f"unrecognized Grok conversation wrapper: {conv_index}")
            continue
        recognized += 1
        conv = wrapper["conversation"]
        conv_id = str(conv.get("id") or conv.get("_id") or f"conversation-{conv_index}")
        title = str(conv.get("title") or "")
        before = len(messages)
        for response_index, response_wrapper in enumerate(wrapper["responses"]):
            response = response_wrapper.get("response") if isinstance(response_wrapper, dict) else None
            if not isinstance(response, dict):
                warnings.append(f"unrecognized Grok response wrapper: {conv_id}:{response_index}")
                continue
            if any(key in response for key in ("thinking_trace", "agent_thinking_traces")):
                omitted_private_reasoning = True
            text = response.get("message")
            has_attachments = bool(response.get("generated_image_urls") or response.get("file_attachments"))
            if not isinstance(text, str) or not text.strip():
                if has_attachments:
                    warnings.append(f"Grok response had attachment-only content: {conv_id}:{response_index}")
                else:
                    warnings.append(f"unrecognized Grok response payload: {conv_id}:{response_index}")
                continue
            sender = str(response.get("sender") or "assistant")
            actor = "user" if sender.casefold() in {"human", "user"} else "assistant"
            source_id = str(response.get("_id") or response.get("id") or f"{conv_id}:{response_index}")
            metadata = {
                "provider_layout": "grok-prod-backend-v1",
                "model": response.get("model"),
                "parent_response_id": response.get("parent_response_id"),
            }
            if isinstance(response.get("generated_image_urls"), list):
                metadata["generated_image_count"] = len(response["generated_image_urls"])
            if isinstance(response.get("file_attachments"), list):
                metadata["file_attachment_count"] = len(response["file_attachments"])
            messages.append(ParsedMessage(
                text=text.strip(),
                actor=actor,
                conversation_id=conv_id,
                source_local_id=source_id,
                timestamp=_grok_timestamp(response.get("create_time")),
                title=title,
                metadata=metadata,
            ))
        if len(messages) == before:
            warnings.append(f"recognized Grok conversation had no textual messages: {conv_id}")
    if recognized == 0:
        raise AdapterError("Grok conversation layout not recognized")
    if omitted_private_reasoning:
        warnings.append("Grok thinking_trace/agent_thinking_traces fields were intentionally excluded from staging")
    return ParseResult(
        source_type="grok-account-export",
        adapter_id="grok",
        adapter_version=ADAPTER_PROTOCOL_VERSION,
        layout_id="grok-prod-backend-v1",
        parse_status=_status(warnings),
        messages=messages,
        warnings=warnings,
    )


_ROLE_PREFIX = re.compile(r"^\s*(User|Human|Assistant|AI|ChatGPT|Claude|Gemini|Grok)\s*:\s*(.*)$", re.IGNORECASE)


def _normalized_role(label: str) -> str:
    return "user" if label.casefold() in {"user", "human"} else "assistant"


def parse_role_prefixed_text(text: str, *, source_id: str = "browser-chat") -> ParseResult:
    messages: list[ParsedMessage] = []
    current_role: str | None = None
    current_parts: list[str] = []
    message_index = 0

    def flush() -> None:
        nonlocal current_parts, current_role, message_index
        body = "\n".join(current_parts).strip()
        if current_role is not None and body:
            messages.append(ParsedMessage(
                text=body,
                actor=current_role,
                conversation_id=source_id,
                source_local_id=f"{source_id}:{message_index}",
                metadata={"provider_layout": "browser-role-prefixed-text-v1"},
            ))
            message_index += 1
        current_parts = []

    for line in text.splitlines():
        match = _ROLE_PREFIX.match(line)
        if match:
            flush()
            current_role = _normalized_role(match.group(1))
            current_parts = [match.group(2)] if match.group(2) else []
        elif current_role is not None:
            current_parts.append(line)
    flush()
    warnings: list[str] = []
    if not messages:
        body = text.strip()
        if not body:
            raise AdapterError("browser-chat text was empty")
        warnings.append("no role prefixes were recognized; imported as one unassigned browser-chat document")
        messages.append(ParsedMessage(
            text=body,
            actor=None,
            conversation_id=source_id,
            source_local_id=f"{source_id}:document",
            kind="browser_chat_document",
            metadata={"provider_layout": "browser-text-document-v1"},
        ))
    return ParseResult(
        source_type="browser-chat-archive",
        adapter_id="browser-chat",
        adapter_version=ADAPTER_PROTOCOL_VERSION,
        layout_id="browser-role-prefixed-text-v1" if not warnings else "browser-text-document-v1",
        parse_status=_status(warnings),
        messages=messages,
        warnings=warnings,
    )


_HTML_VOID_ELEMENTS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr",
}


class _RoleHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.capture_role: str | None = None
        self.capture_depth = 0
        self.capture_parts: list[str] = []
        self.messages: list[tuple[str, str]] = []
        self.all_text: list[str] = []

    @staticmethod
    def role_for(tag: str, attrs: list[tuple[str, str | None]]) -> str | None:
        values = {key.casefold(): (value or "") for key, value in attrs}
        for key in ("data-message-author-role", "data-role", "data-author"):
            value = values.get(key, "").casefold()
            if value in {"user", "human"}:
                return "user"
            if value in {"assistant", "ai", "bot", "model"}:
                return "assistant"
        classes = values.get("class", "").casefold()
        if any(token in classes for token in ("user-message", "message-user", "human-message")):
            return "user"
        if any(token in classes for token in ("assistant-message", "message-assistant", "ai-message", "bot-message")):
            return "assistant"
        return None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if self.capture_role is not None:
            if tag not in _HTML_VOID_ELEMENTS:
                self.capture_depth += 1
            return
        role = self.role_for(tag, attrs)
        if role is not None:
            self.capture_role = role
            self.capture_depth = 1
            self.capture_parts = []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.capture_role is None:
            role = self.role_for(tag.casefold(), attrs)
            if role is not None:
                self.capture_role = role
                self.capture_depth = 1
                self.capture_parts = []
                self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self.capture_role is None:
            return
        self.capture_depth -= 1
        if self.capture_depth == 0:
            text = " ".join(part for part in self.capture_parts if part).strip()
            if text:
                self.messages.append((self.capture_role, text))
            self.capture_role = None
            self.capture_parts = []

    def handle_data(self, data: str) -> None:
        stripped = data.strip()
        if not stripped:
            return
        self.all_text.append(stripped)
        if self.capture_role is not None:
            self.capture_parts.append(stripped)


def parse_browser_html(html: str, *, source_id: str = "browser-chat") -> ParseResult:
    parser = _RoleHTMLParser()
    parser.feed(html)
    if parser.messages:
        messages = [
            ParsedMessage(
                text=text,
                actor=role,
                conversation_id=source_id,
                source_local_id=f"{source_id}:{index}",
                metadata={"provider_layout": "browser-html-role-attributes-v1"},
            )
            for index, (role, text) in enumerate(parser.messages)
        ]
        return ParseResult(
            source_type="browser-chat-archive",
            adapter_id="browser-chat",
            adapter_version=ADAPTER_PROTOCOL_VERSION,
            layout_id="browser-html-role-attributes-v1",
            parse_status="exact",
            messages=messages,
            warnings=[],
        )
    plain = "\n".join(parser.all_text)
    result = parse_role_prefixed_text(plain, source_id=source_id)
    warnings = ["HTML role attributes were not recognized; fell back to visible-text parsing", *result.warnings]
    return ParseResult(
        source_type=result.source_type,
        adapter_id=result.adapter_id,
        adapter_version=result.adapter_version,
        layout_id="browser-html-text-fallback-v1",
        parse_status="partial",
        messages=[
            ParsedMessage(
                text=item.text,
                actor=item.actor,
                conversation_id=item.conversation_id,
                source_local_id=item.source_local_id,
                timestamp=item.timestamp,
                title=item.title,
                kind=item.kind,
                metadata={**item.metadata, "provider_layout": "browser-html-text-fallback-v1"},
            )
            for item in result.messages
        ],
        warnings=warnings,
    )


def _dot_get(value: Any, path: str) -> Any:
    if path == "":
        return value
    current = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise AdapterError(f"plugin path not found: {path}")
        current = current[part]
    return current


def _reject_unknown_keys(mapping: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise AdapterError(f"plugin {label} has unknown properties: {', '.join(unknown)}")


def validate_plugin_descriptor(descriptor: Any) -> dict[str, Any]:
    """Validate the complete published data-only plugin contract without runtime dependencies."""
    if not isinstance(descriptor, dict):
        raise AdapterError("plugin descriptor must be a JSON object")
    _reject_unknown_keys(
        descriptor,
        {"protocol", "schema_version", "id", "version", "description", "input", "conversation", "message", "role_map"},
        "descriptor",
    )
    if descriptor.get("protocol") != "AI-CONTEXT/ADAPTER-PLUGIN":
        raise AdapterError("invalid plugin protocol")
    if descriptor.get("schema_version") != ADAPTER_PROTOCOL_VERSION:
        raise AdapterError("unsupported plugin schema version")
    if not isinstance(descriptor.get("id"), str) or not descriptor["id"].strip():
        raise AdapterError("plugin id must be a non-empty string")
    if not isinstance(descriptor.get("version"), str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", descriptor["version"]):
        raise AdapterError("plugin version must be semantic x.y.z")
    if "description" in descriptor and not isinstance(descriptor["description"], str):
        raise AdapterError("plugin description must be a string")

    input_spec = descriptor.get("input")
    conversation = descriptor.get("conversation")
    message = descriptor.get("message")
    if not isinstance(input_spec, dict) or not isinstance(conversation, dict) or not isinstance(message, dict):
        raise AdapterError("plugin requires input, conversation, and message mapping objects")

    _reject_unknown_keys(input_spec, {"kind", "root_path", "filename"}, "input")
    _reject_unknown_keys(conversation, {"messages_path", "id_path", "title_path"}, "conversation")
    _reject_unknown_keys(message, {"text_path", "actor_path", "id_path", "timestamp_path"}, "message")

    if input_spec.get("kind") != "json":
        raise AdapterError("plugin input.kind must be json")
    if not isinstance(input_spec.get("root_path"), str):
        raise AdapterError("plugin input.root_path must be a string")
    if "filename" in input_spec and (not isinstance(input_spec["filename"], str) or not input_spec["filename"]):
        raise AdapterError("plugin input.filename must be a non-empty string")
    if not isinstance(conversation.get("messages_path"), str) or not conversation["messages_path"]:
        raise AdapterError("plugin conversation.messages_path must be a non-empty string")
    for key in ("id_path", "title_path"):
        if key in conversation and not isinstance(conversation[key], str):
            raise AdapterError(f"plugin conversation.{key} must be a string")
    if not isinstance(message.get("text_path"), str) or not message["text_path"]:
        raise AdapterError("plugin message.text_path must be a non-empty string")
    for key in ("actor_path", "id_path", "timestamp_path"):
        if key in message and not isinstance(message[key], str):
            raise AdapterError(f"plugin message.{key} must be a string")

    role_map = descriptor.get("role_map")
    if role_map is not None:
        if not isinstance(role_map, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in role_map.items()):
            raise AdapterError("plugin role_map must be an object with string values")
    return descriptor


def parse_plugin_json(data: Any, descriptor: dict[str, Any]) -> ParseResult:
    descriptor = validate_plugin_descriptor(descriptor)
    input_spec = descriptor["input"]
    conversation_spec = descriptor["conversation"]
    message_spec = descriptor["message"]
    root = _dot_get(data, input_spec["root_path"])
    if not isinstance(root, list):
        raise AdapterError("plugin root_path must resolve to an array")
    messages: list[ParsedMessage] = []
    warnings: list[str] = []
    role_map = descriptor.get("role_map") if isinstance(descriptor.get("role_map"), dict) else {}
    for conv_index, conv in enumerate(root):
        if not isinstance(conv, dict):
            warnings.append(f"plugin skipped non-object conversation: {conv_index}")
            continue
        try:
            raw_messages = _dot_get(conv, conversation_spec["messages_path"])
        except AdapterError:
            warnings.append(f"plugin conversation missing messages path: {conv_index}")
            continue
        if not isinstance(raw_messages, list):
            warnings.append(f"plugin messages path was not an array: {conv_index}")
            continue

        id_path = conversation_spec.get("id_path")
        if isinstance(id_path, str) and id_path:
            try:
                conv_id = _stringify(_dot_get(conv, id_path)).strip()
            except AdapterError:
                warnings.append(f"plugin conversation id path missing: {conv_index}")
                conv_id = ""
        else:
            conv_id = ""
        if not conv_id:
            conv_id = f"conversation-{conv_index}"

        title = ""
        title_path = conversation_spec.get("title_path")
        if isinstance(title_path, str) and title_path:
            try:
                title = _stringify(_dot_get(conv, title_path))
            except AdapterError:
                warnings.append(f"plugin title path missing: {conv_id}")

        for msg_index, item in enumerate(raw_messages):
            if not isinstance(item, dict):
                warnings.append(f"plugin skipped non-object message: {conv_id}:{msg_index}")
                continue
            try:
                text = _stringify(_dot_get(item, message_spec["text_path"])).strip()
            except AdapterError:
                warnings.append(f"plugin text path missing: {conv_id}:{msg_index}")
                continue
            if not text:
                warnings.append(f"plugin message text was empty: {conv_id}:{msg_index}")
                continue

            actor = None
            actor_path = message_spec.get("actor_path")
            if isinstance(actor_path, str) and actor_path:
                try:
                    raw_actor = _stringify(_dot_get(item, actor_path))
                    actor = str(role_map.get(raw_actor, raw_actor)) if raw_actor else None
                except AdapterError:
                    warnings.append(f"plugin actor path missing: {conv_id}:{msg_index}")

            timestamp = None
            timestamp_path = message_spec.get("timestamp_path")
            if isinstance(timestamp_path, str) and timestamp_path:
                try:
                    timestamp = _stringify(_dot_get(item, timestamp_path)) or None
                except AdapterError:
                    warnings.append(f"plugin timestamp path missing: {conv_id}:{msg_index}")

            source_local_id = f"{conv_id}:{msg_index}"
            message_id_path = message_spec.get("id_path")
            if isinstance(message_id_path, str) and message_id_path:
                try:
                    source_local_id = _stringify(_dot_get(item, message_id_path)) or source_local_id
                except AdapterError:
                    warnings.append(f"plugin message id path missing: {conv_id}:{msg_index}")

            messages.append(ParsedMessage(
                text=text,
                actor=actor,
                conversation_id=conv_id,
                source_local_id=source_local_id,
                timestamp=timestamp,
                title=title,
                metadata={"provider_layout": f"plugin:{descriptor['id']}@{descriptor['version']}"},
            ))
    if not messages:
        raise AdapterError("plugin produced no parseable messages")
    return ParseResult(
        source_type="community-adapter-export",
        adapter_id=f"plugin:{descriptor['id']}@{descriptor['version']}",
        adapter_version=ADAPTER_PROTOCOL_VERSION,
        layout_id=f"plugin:{descriptor['id']}@{descriptor['version']}",
        parse_status=_status(warnings),
        messages=messages,
        warnings=warnings,
    )
