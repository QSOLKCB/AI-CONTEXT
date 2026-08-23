# Grok account export adapter

## Status

- Adapter id: `grok`
- Adapter implementation version: `0.1.0`
- Recognized layout: `grok-prod-backend-v1`
- Export surface: official Grok account-data download
- Format stability: official download surface, undocumented JSON schema

xAI documents that users can download account data from Grok Data Controls. Observed exports commonly contain a monolithic `prod-grok-backend.json` under an export-data directory. AI-CONTEXT treats that JSON shape as unstable provider input.

Official reference:

- https://x.ai/legal/faq

Observed-format references used to design synthetic fixtures:

- https://portable-ai-memory.org/providers/grok/
- https://github.com/ChrystianSchutz/ThreadShelf

## Recognized layout

The adapter expects:

```text
{
  "conversations": [
    {
      "conversation": {...},
      "responses": [
        {"response": {...}}
      ]
    }
  ]
}
```

Conversation fields may include `id`, `_id`, and `title`. Message fields may include:

```text
_id or id
sender
message
create_time
model
parent_response_id
generated_image_urls
file_attachments
```

Observed message timestamps may use MongoDB extended JSON such as:

```json
{"$date":{"$numberLong":"1771000000000"}}
```

The reference adapter converts that timestamp deterministically to UTC ISO 8601.

## Thinking-trace boundary

Observed Grok exports may include fields such as:

```text
thinking_trace
agent_thinking_traces
```

AI-CONTEXT intentionally does not copy those fields into ordinary staging observations. A warning is emitted and the import becomes `partial` when such fields are present.

This prevents provider-private reasoning traces from being silently reclassified as ordinary personal memory. The visible conversational `message` is still imported.

## Role normalization

Observed sender values vary. The adapter treats `human` and `user` case-insensitively as `user`; other sender values are normalized to `assistant` for the current observed layout.

## Migration fixtures

See:

```text
fixtures/provider-drift/grok/
```

Fixtures include the wrapped conversation/response shape, extended-JSON timestamps, and a synthetic thinking-trace field used only to verify exclusion behavior.
