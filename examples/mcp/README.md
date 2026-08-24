# MCP adapter example

This directory demonstrates how an MCP host can expose AI-CONTEXT **read-only interoperability surfaces** without becoming a new memory or disclosure authority.

`tools.json` is intentionally data-only. An MCP SDK implementation can map each descriptor to the corresponding local CLI invocation and return stdout as structured tool output.

Recommended exposed tools:

- capability discovery;
- candidate-only index search;
- routed-bundle validation;
- signed-bundle receipt verification.

Do not expose direct canonical-memory writes, automatic candidate approval, sensitivity downgrade, or routing-policy bypass through an MCP adapter.

The authority chain remains:

```text
canonical memory
  -> Phase 6 routing/policy
  -> routed bundle
  -> optional signed integrity receipt
  -> MCP/tool transport
```

A tool result does not become memory merely because an agent can call it.

For an SDK-backed implementation, preserve the argument validation and local-only path assumptions of the underlying CLI. Do not substitute shell interpolation for structured process arguments.
