# Gemini Takeout adapter

## Status

- Adapter id: `gemini`
- Adapter implementation version: `0.1.0`
- Recognized layout family: `gemini-myactivity-*`
- Export surface: official Google Takeout / My Activity export for Gemini Apps
- Format stability: official export surface, undocumented/variable activity schema
- Parse status: always at least `partial`

Google documents that users can download Gemini Apps activity, including chats, generated media, and uploads, through Google Takeout. The activity export can be delivered in JSON or HTML depending on Takeout settings. The exact JSON schema is provider-controlled and is not treated as stable by AI-CONTEXT.

Official reference:

- https://support.google.com/gemini/answer/16920332

Observed-format reference used to design synthetic drift fixtures:

- https://portable-ai-memory.org/providers/google/

## Recommended export

Use Google Takeout and select:

```text
My Activity
  -> Gemini Apps
```

Prefer JSON for deterministic machine parsing. A commonly observed path is:

```text
Takeout/My Activity/Gemini Apps/MyActivity.json
```

Do not confuse this with the separate `Gemini` Takeout product, which may contain Gems or other product data rather than complete Gemini Apps activity.

## Recognized activity variants

The Phase 3 parser handles these observed variants per activity event:

1. `details[]` entries named `Request` and `Response`;
2. `userInteractions[].userInteraction.request/response`, including serialized JSON strings;
3. a conservative `safeHtmlItem[]` fallback paired with a `Prompted ...` activity title.

Conversation ids are extracted from `titleUrl` paths shaped like:

```text
/app/c/<conversation-id>
```

When no conversation id is available, the importer uses a deterministic activity-local fallback id rather than inventing cross-event grouping.

## Why parse status is `partial`

Even when the observed layout is fully recognized, Gemini Takeout is an activity log rather than a guaranteed complete conversation archive. Community-verified exports show that response bodies may be absent or truncated. AI-CONTEXT therefore refuses to label the import `exact`.

This is deliberate epistemic metadata, not a parser failure.

## Migration fixtures

See:

```text
fixtures/provider-drift/gemini/
```

The fixtures cover `details`, `userInteractions`, and `safeHtmlItem` variants using synthetic content only.
