# WeCom V1 Specification

## Verified scope

Real WeCom WebSocket connection, inbound text and Markdown file handling, proactive Markdown/media sending, Markdown approval, SQLite restart recovery, and message-id persistence have been verified. Production proposal generation and real WeCom transport were verified separately. A new real-WeCom clear-request-to-proposal-to-file combined run has not been performed. V2 has not started.

## Modes

- `PROPOSAL_ENABLED` is normal V1 production mode. A clear request starts one background ProposalRunner Task; its internal planning, section, and review calls belong to that one Task.
- `PROPOSAL_DISABLED` is communication-only live-test mode, selected with `--disable-proposal`. Both vague and clear text remain `CLARIFYING` and reply that this round will not generate a proposal. It never submits ProposalRunner work.

Argument parsing errors exit before startup; they cannot silently select enabled mode. On restart, a persisted `GENERATING_MD` Task is marked `FAILED` with `interrupted_restart`; it is never rerun automatically.

## State and safety

Tasks are isolated by `(userid, chatid)` in a dedicated SQLite file. A message-id maps to one Task persistently; confirmation and uploaded Markdown ids are also recorded. Confirmation and upload act only when exactly one `WAITING_MD_APPROVAL` Task exists for that conversation. Uploaded content is UTF-8 Markdown and is always written to a fixed `approved.md` path, never to a caller-controlled filename.

User-facing failures contain only a safe stage and Task ID. Secrets, tokens, authorization headers, cookies, signed URLs, raw WebSocket frames, and full identity values must not be persisted in reports or logs.

## Current conclusion

`YES_WITH_LIMITATIONS`: the real communication paths are verified, but communication-only tests must use `--disable-proposal`; the early live test accidentally started one proposal Task before this guard was added. V1 intentionally does not include Word/PPT or V2 behavior.
