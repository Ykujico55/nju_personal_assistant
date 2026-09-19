# nju.smail extension

Read-only IMAP synchronization, thread/contact/attachment provenance, versioned
reply drafts and controlled SMTP send for the `nju.smail` account.

- The extension never opens IMAP or SMTP sockets and never holds the client
  password: reading goes through the host `host.mail.*` capabilities and the
  password is resolved by the host mail broker from an opaque `SecretHandle`.
- Sending is an independent `EXTERNAL_WRITE` capability. `smail.send` only
  materializes the exact MIME bytes for the current draft version; the host
  executor sends them behind the Tool Gateway, R2 approval and SideEffect
  Outbox. An edited draft invalidates the previous approval binding.
- Message identity is `(account, folder, UIDVALIDITY, UID)` for locations and
  `Message-ID` (falling back to a normalized content hash) for logical
  messages, so repeated syncs, restarts and UIDVALIDITY resets converge.
- Partial recipient rejection keeps per-recipient detail; an UNKNOWN transport
  outcome is never retried automatically and converges through read-only Sent
  folder reconciliation or an explicit user decision.

Configuration (non-secret only) is stored through the host extension config
channel (`accounts`, `folders`, sync bounds). The client password is created in
the local settings flow and never appears in configuration, logs, RPC, the
database or test fixtures.

This extension is part of F06 and remains `IN_PROGRESS` pending independent
acceptance; no real mailbox has been contacted.
