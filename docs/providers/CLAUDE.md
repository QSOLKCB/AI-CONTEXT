# Claude export adapter

## Status

- Adapter id: `claude`
- Adapter implementation version: `0.1.0`
- Recognized layout: `claude-chat-messages-v1`
- Export surface: official Claude data export
- Format stability: semi-stable input, not an AI-CONTEXT contract

Anthropic documents that user data exports include conversation data and account data. AI-CONTEXT treats the emitted JSON shape as provider-controlled and version-sensitive.

Official reference:

- https://support.claude.com/en/articles/9450526-export-your-claude-data

## Observed layout

The reference parser recognizes a top-level array where each conversation contains a `chat_messages` array. Conversation identity/title fields may include:

```text
uuid or id
name or title
chat_messages[]
```

Recognized message fields include:

```text
uuid or id
sender
created_at
text
```

`text` may be a string or a list. List elements are deterministically stringified and joined.

## Drift rules

`exact` means every top-level conversation matched the recognized layout and every recognized conversation produced at least one parseable message.

`partial` is emitted when:

- a recognized conversation contains no parseable messages;
- some top-level entries do not match the expected shape; or
- a compatible export contains fields the reference parser cannot preserve exactly.

A file with zero recognized conversations fails closed.

## Migration fixtures

See:

```text
fixtures/provider-drift/claude/
```

Fixtures are synthetic and cover current, partial-drift, and unsupported-shape cases.

## Non-authority rule

Claude output imported from an export is evidence of a prior model response, not evidence that the response was factually correct. Promotion remains explicit.
