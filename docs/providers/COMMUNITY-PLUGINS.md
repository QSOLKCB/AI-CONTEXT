# Community adapter plugins

AI-CONTEXT Phase 3 supports a deliberately constrained community adapter interface for simple JSON conversation exports.

## Security model

A community adapter plugin is **data, not code**.

The reference implementation does not import Python modules, execute hooks, run shell commands, evaluate JSONPath expressions, or install packages from a plugin. A plugin is a JSON mapping descriptor validated against:

```text
spec/adapter-plugin.schema.json
```

This keeps the private archive parser from becoming an arbitrary-code execution surface.

## Descriptor format

Example:

```json
{
  "protocol": "AI-CONTEXT/ADAPTER-PLUGIN",
  "schema_version": "0.1.0",
  "id": "community.example-chat",
  "version": "1.0.0",
  "input": {
    "kind": "json",
    "root_path": "conversations",
    "filename": "example-export.json"
  },
  "conversation": {
    "messages_path": "messages",
    "id_path": "id",
    "title_path": "title"
  },
  "message": {
    "text_path": "content.text",
    "actor_path": "role",
    "id_path": "id",
    "timestamp_path": "created_at"
  },
  "role_map": {
    "human": "user",
    "bot": "assistant"
  }
}
```

Paths are simple dot-separated object keys. They are not a general expression language.

## Invocation

```bash
python3 tools/provider_import.py ~/my-ai-context export.json \
  --adapter plugin \
  --plugin my-adapter.json
```

A plugin may also name one JSON file by basename inside a ZIP or directory via `input.filename`.

## Receipt identity

The canonical JSON SHA-256 of the plugin descriptor is embedded into the `requested_adapter` identity:

```text
plugin:<sha256>
```

Changing a mapping therefore changes the import receipt identity even if the source archive is unchanged.

The resolved adapter id records the plugin's declared id/version while `adapter.version` records the AI-CONTEXT adapter protocol version.

## Deliberate limits

Phase 3 plugins support JSON conversation/message mapping only. Complex proprietary formats should be implemented as reviewed built-in adapters or converted to a simpler JSON form before import.

A plugin cannot promote memory. It only produces staging observations.
