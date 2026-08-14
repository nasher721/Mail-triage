# Local Outlook Email Triage (Python)

Reads unread Inbox mail through Microsoft Graph delegated OAuth, screens each message with a **local Ollama model**, drafts replies where a reply is warranted, and then lets a **local tool-calling agent sort the mail in Outlook**: triage folders, categories, unsent reply drafts, and optionally marking messages read.

Nothing is ever sent, forwarded, or deleted. The application does not hold the `Mail.Send` scope, and no send/forward/delete call exists anywhere in the code (enforced by a test).

## What runs where

| Stage | Component | Data leaving the machine |
| --- | --- | --- |
| Read unread mail | Outlook-in-Edge session (`--owa`) or Microsoft Graph | Microsoft only |
| Screen + draft reply | Ollama on `127.0.0.1` | none |
| Plan mailbox actions | Ollama tool-calling agent | none |
| Apply actions | Microsoft Graph | Microsoft only |

`TRIAGE_BACKEND=openai` is still available for screening, but it requires a key and an explicit `EXTERNAL_AI_APPROVED=true`. The sorting agent is always local.

## The sorting agent, and what bounds it

`src/email_triage/agent.py` runs a real tool-calling loop against Ollama. It is deliberately fenced in:

- **It never sees the email body.** It receives only the validated screening result, plus the sender address and a truncated subject explicitly labelled untrusted.
- **Its tool surface is computed per message.** A clinical or low-confidence message is offered only `tag_message` and `file_message`, and `file_message`'s folder enum contains just `AI Triage/Needs Review`. `draft_reply` is not offered at all unless screening produced an approved reply.
- **Reply text cannot be rewritten.** The draft body must match the screened `suggested_reply` verbatim; the agent decides only *whether* to create the draft.
- **Every call is re-validated after the fact** by the policy gate in `src/email_triage/actions.py`. Rejections are returned to the model as tool errors so it can correct itself.
- **Send, forward, and delete are not representable.** There is no such tool and no such Graph call, so no prompt in an email body can produce one.
- **If the agent stalls, misbehaves, or Ollama is down**, the deterministic plan derived from the screening result is used instead. The `plan_source` field on every record records which one ran.

## Preserved screening behavior

- Routes: `needs_review`, `needs_reply`, `no_reply`; urgency `urgent`/`soon`/`routine`; topics clinical, scheduling, administrative, education/research, other.
- Summary, priority score 1–5, action items, deadline, rationale, and a professional suggested reply only when required. Replies end with `Best,` / `Nick`.
- Calendar items are skipped; no-reply senders route deterministically to `no_reply`.
- Prompt injection in a body is intercepted locally and sent to manual review. Clinical interception also applies when screening is not local.
- Private and Confidential messages follow normal routing; sensitivity is not sent to the model.
- Attachment names and contents are never requested or transmitted — only a `hasAttachments` Boolean.
- Provider or schema failures fail closed to manual review.

`var/review_queue.jsonl` records metadata and the structured result, `var/applied_actions.jsonl` records every action planned or applied. Neither stores the message body. Files under `var/` are owner-only and Git-ignored.

## Microsoft Entra setup

1. Create a single-tenant app registration in Microsoft Entra ID.
2. Enable public-client/device-code authentication.
3. Add the delegated Microsoft Graph permission you intend to use:
   - `Mail.Read` — screening and preview only.
   - `Mail.ReadWrite` — required for `--apply` (folders, categories, drafts, move, read state). **Do not add `Mail.Send`.**
4. Grant consent per institutional policy.
5. Record the Directory (tenant) ID and Application (client) ID. No client secret is needed.

Changing between read and read-write invalidates the cached token, so the device-code prompt appears again on the first `--apply` run. If Conditional Access blocks device code, use the organization's approved brokered or browser-based OAuth flow rather than requesting an exception.

Before sending mailbox content anywhere off-device (the `openai` backend, or a non-loopback `OLLAMA_HOST`), obtain institutional privacy/security approval. This repository does not itself establish compliance.

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate && python -m pip install -e .
```

The runtime uses only the Python standard library. Ollama must be running locally:

```bash
ollama serve && ollama pull qwen3:8b
```

`qwen3:8b` is the default and fits an 18 GB Apple Silicon machine; the classifier falls back to the strongest installed tag if it is missing.

## Run

If Outlook is already open in Microsoft Edge (Outlook on the web), you do **not** need an Entra admin app, `.eml` export, or Windows COM/`pywin32`. COM only talks to desktop Outlook on Windows. On macOS the script attaches to the signed-in Edge tab and reuses that session.

One-time setup, then leave Edge open:

```bash
python3 -m pip install --user playwright
bash scripts/open_outlook_in_edge.sh
python3 email_triage_standalone.py --owa --apply --watch
```

`--watch` keeps screening every 5 minutes with no further input. Replies are saved as **unsent drafts**. Nothing is sent.

Preview against synthetic mail, no Microsoft account needed:

```bash
email-triage --input samples/inbox.jsonl
```

Preview against the real mailbox — reads unread mail, changes nothing:

```bash
export MS_TENANT_ID="your-tenant-id" MS_CLIENT_ID="your-client-id"
email-triage
```

Sort the mailbox for real — moves messages into `AI Triage/…`, applies categories, saves unsent reply drafts:

```bash
email-triage --apply
```

Also mark filed mail as read (never applied to `needs_review` messages):

```bash
email-triage --apply --mark-read
```

Other flags: `--no-agent` uses the deterministic plan only; `--include-previously-processed` re-screens message IDs already in the local state file.

Each processed message prints one JSON line containing the screening result, `plan_source`, and the action outcomes. Exit codes: `0` success (or a skipped run because another copy holds the lock), `1` at least one action failed, `2` configuration or Graph error.

## Unattended operation

For Outlook already open in Edge, keep that window running after `scripts/open_outlook_in_edge.sh` and use `--owa --watch`. No `--login` and no Entra app:

```bash
python tools/make_launch_agent.py --owa --apply --interval-minutes 15
```

`--watch` is the other hands-off option: one long-lived process retries while Edge stays open. If Edge is quit, that cycle logs an error and waits for the next interval instead of hanging.

For a registered Graph app, every run is already non-interactive except the very first Microsoft sign-in:

**1. Sign in once, from a terminal.** This caches a refresh token under `var/`:

```bash
email-triage --login --apply
```

Scheduled runs then renew silently. If standard input is not a terminal — which is always true under launchd — the CLI refuses to start a device-code prompt and exits `2` with instructions instead of hanging for 15 minutes. `--non-interactive` forces the same behavior in a terminal.

The refresh token stays valid as long as it keeps being used, so a job running every 15 minutes never needs a second sign-in unless your tenant revokes the session, your password changes, or you switch between read-only and `--apply` (the scopes differ, so the cached token is discarded).

**2. Put the configuration in a file.** A scheduled job inherits no shell profile, so `export` lines are useless to it:

```bash
mkdir -p ~/.config/email-triage
install -m 600 .env.example ~/.config/email-triage/env
$EDITOR ~/.config/email-triage/env
```

`--env-file` refuses to read a file that is group- or world-readable, and variables already present in the environment win over the file.

**3. Generate the launchd agents:**

```bash
python tools/make_launch_agent.py --apply --mark-read --interval-minutes 15
```

This writes `dist/launchd/com.emailtriage.sort.plist` (the triage job) and `dist/launchd/com.emailtriage.ollama.plist` (a `KeepAlive` agent so `ollama serve` is always up) and prints the exact `cp` and `launchctl bootstrap` commands. It does not install them — a launch agent is persistent system configuration, so that step stays with you. Skip the second agent with `--skip-ollama-agent` if the Ollama app already starts at login.

Once loaded, each run reads unread mail, screens and drafts locally, files everything into `AI Triage/…`, and appends to `var/applied_actions.jsonl`. Output goes to `var/logs/triage.out.log` and `triage.err.log`.

**Overlap, restarts, and failures.** A lock file (`var/triage.lock`) means a slow run is never overtaken by the next tick — the second copy logs one line and exits `0`. If Ollama is down or the network is out, that run exits non-zero and the next tick simply tries again; `var/processed_message_ids.json` prevents any message from being screened twice.

**What still needs you.** Drafts are created unsent, in Outlook, addressed and threaded correctly. Reading and sending them stays a human decision — automatic sending is out of scope by design, not by omission.

## Single-file bundle

`email_triage_standalone.py` at the repository root is a generated standalone copy for machines where installing the package is impractical. It is named so that it cannot shadow the installed `email_triage` package. Do not edit it directly:

```bash
python tools/build_single_file.py          # regenerate
python tools/build_single_file.py --check  # fail if stale
python email_triage_standalone.py --self-test  # run the bundled tests
```

## Test

Tests use synthetic messages and never call Microsoft or a model provider:

```bash
python -m unittest discover -s tests -t tests -v
```

## Boundary

Automatic sending stays permanently out of scope. Replies are prepared as drafts in Outlook for a human to read, edit, and send.

## Official references

- [Microsoft: Basic authentication is disabled in Exchange Online](https://learn.microsoft.com/en-us/exchange/clients-and-mobile-in-exchange-online/deprecation-of-basic-authentication-exchange-online)
- [Microsoft Graph: list messages](https://learn.microsoft.com/en-us/graph/api/user-list-messages?view=graph-rest-1.0)
- [Microsoft Graph: createReply](https://learn.microsoft.com/en-us/graph/api/message-createreply?view=graph-rest-1.0)
- [Microsoft Graph: move message](https://learn.microsoft.com/en-us/graph/api/message-move?view=graph-rest-1.0)
- [Microsoft identity platform: device authorization grant](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-device-code)
- [Ollama: tool support](https://ollama.com/blog/tool-support)
