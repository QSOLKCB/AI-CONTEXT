# ChatGPT export adapter

## Status

- Adapter id: `chatgpt`
- Adapter implementation version: `0.1.0`
- Recognized layout: `chatgpt-conversations-mapping-v1`
- Export surface: official ChatGPT data export
- Format stability: semi-stable input, not an AI-CONTEXT contract

OpenAI documents that ChatGPT exports contain conversation JSON, and larger exports may contain numbered conversation JSON files. AI-CONTEXT does not assume the provider schema is permanent.

Official references:

- https://help.openai.com/en/articles/7260999-how-do-i-export-my-chatgpt-history-and-data
- https://help.openai.com/en/articles/9106926-transfer-exported-conversations-between-chatgpt-accounts

## Observed layout

The current reference parser recognizes a top-level array of conversations where each conversation contains a `mapping` object. Each mapping node may contain a `message` object with:

```text
message.id
message.author.role
message.create_time
message.content.parts[]
```

Conversation metadata commonly includes:

```text
id
title
mapping
```

The mapping is graph-shaped. The reference adapter orders parseable messages by `create_time` and then node id for deterministic staging; it does not claim to reconstruct every provider-internal graph semantic.

## Drift rules

`exact` means every top-level conversation matched the recognized layout and every recognized conversation produced at least one parseable textual message.

`partial` is emitted when:

- a recognized conversation contains no parseable messages;
- some top-level entries do not match the expected conversation shape; or
- a future known-compatible extension can be read only partially.

A file with zero recognized conversations fails closed as an unrecognized ChatGPT layout.

## Migration fixtures

See:

```text
fixtures/provider-drift/chatgpt/
```

The fixtures are synthetic. They intentionally include a current-layout case, a partially recognized drift case, and an unsupported future-shape case.

## Non-authority rule

An imported assistant message is a staging observation. It is not verified fact and does not become canonical memory without promotion under AI-CONTEXT policy.
