# Email Archive Evidence Adapter

AI-CONTEXT Phase 4 supports standards-based email archive ingestion without connecting to a live mailbox.

The reference adapter accepts:

- `.eml` RFC 5322/MIME messages;
- `.mbox` / `.mbx` mailbox archives;
- ZIPs or directories containing those formats.

Gmail officially supports exporting mail through Google Takeout. Exported mail includes message content, headers, attachments, and Gmail label data. Gmail preserves labels in the `X-Gmail-Labels` header. Individual Gmail messages can also be downloaded as `.eml` files.

Official references:

- https://support.google.com/mail/answer/10016932
- https://support.google.com/accounts/answer/3024190
- https://support.google.com/mail/answer/9261412

## Usage

MBOX / Takeout mail archive:

```bash
python3 tools/evidence_import.py \
  ~/my-ai-context \
  ~/Downloads/All-mail-Including-Spam-and-Trash.mbox \
  --adapter email
```

Single EML:

```bash
python3 tools/evidence_import.py \
  ~/my-ai-context \
  message.eml \
  --adapter email
```

## Evidence representation

Email observations preserve source-specific fields such as:

```text
Message-ID
Subject
Date
From
To
Cc
X-Gmail-Labels
```

Text/plain body content is preferred. If a message has no text/plain body, visible HTML text may be used and the import is marked `partial`.

Attachments are not silently promoted into message body text. Attachment/binary handling belongs to explicit document/binary evidence ingestion rather than implicit MIME flattening.

## Privacy

Email metadata is private evidence. Addresses, subjects, labels, and message bodies remain in the private AI-CONTEXT workspace and must never be copied into this public framework repository.

Email evidence receives source precedence metadata but does not gain factual authority merely because a statement appeared in an email.
