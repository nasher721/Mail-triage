# Mail Triage for macOS

See the [Outlook integration roadmap](docs/outlook-integration-roadmap.md) for the
completed reliability/privacy backlog and prohibited-feature boundaries.

Reads unread Inbox mail through the user's normal Outlook Web session by default—
no Graph app registration, tenant ID, client ID, API key, or administrator
approval required. Microsoft Graph remains an optional organization-approved
backend. Each message is screened by the **AI system you choose** — local Ollama by
default, or Claude, ChatGPT, OpenRouter, OpenCode, LM Studio, Gemini, Groq and
others — and a **tool-calling agent** sorts mail into triage folders with unsent
reply drafts.

Nothing is ever sent, forwarded, or deleted. The application does not hold the `Mail.Send` scope, and no send/forward/delete call exists anywhere in the code (enforced by a test).

## Native Mac app

Mail Triage includes a native SwiftUI application with:

- credential-free Outlook on the Web setup and readiness diagnostics;
- an **AI Providers** screen for connecting Ollama, Claude, ChatGPT, OpenRouter,
  OpenCode, LM Studio, llama.cpp, Azure OpenAI, Gemini, Groq, Mistral, DeepSeek,
  Together, xAI, or any OpenAI-compatible endpoint, with per-provider readiness
  checks and automatic model discovery for local servers;
- API keys stored in the login keychain, with environment variables as a fallback;
- a separate provider for the sorting agent when screening and filing should not
  run on the same system;
- request tuning: model, base URL, temperature, timeout, agent tool rounds,
  message and body limits, and mailbox pages scanned;
- scheduled automatic preview runs, plus JSON/CSV export and route/search
  filtering of results;
- preview and explicitly confirmed apply modes;
- a results browser for routes, priorities, summaries, action items, suggested
  replies, and planned/applied actions;
- a redacted activity log plus a dedicated macOS Settings window;
- local export and bounded macOS Accessibility sources;
- no shell interpolation, embedded credentials, or application-managed token files.

Build and launch a real ad-hoc-signed app bundle:

```bash
./script/build_and_run.sh
```

The bundle is created at `dist/Mail Triage.app`. The same command is exposed as
the Codex desktop Run action through `.codex/environments/environment.toml`.
Useful verification modes are:

```bash
./script/build_and_run.sh --verify
./script/test_mac_app.sh
```

The app bundles `email_triage_standalone.py` and the dedicated Edge helper as
resources. It locates an installed Python 3.11+ runtime and uses the existing
local Playwright installation for OWA. Install the OWA extra once when needed:

```bash
python3 -m pip install 'playwright>=1.40'
```

In the app, choose **Open Outlook Session**, complete the normal Microsoft
sign-in in the dedicated Edge window once, and return to Mail Triage. The app
does not request a tenant ID, client ID, Graph secret, or administrator bypass.

## What runs where

| Stage | Component | Data leaving the machine |
| --- | --- | --- |
| Read unread mail | Outlook-in-Edge session (`--owa`) or Microsoft Graph | Microsoft only |
| Screen + draft reply | the selected provider (default: Ollama on `127.0.0.1`) | none for local providers |
| Plan mailbox actions | the selected sorting-agent provider | none for local providers |
| Apply actions | Outlook-in-Edge or Microsoft Graph | Microsoft only |

Local providers (Ollama, LM Studio, llama.cpp, OpenCode) keep every message body
on this machine. Any hosted provider — and any local provider pointed at a
non-loopback address — requires an explicit `EXTERNAL_AI_APPROVED=true` and an API
key before a run can start.

## Connecting an AI system

`python email_triage_standalone.py --list-providers` prints every supported system
with its default endpoint, default model, and key variable. Select one per run:

```bash
# Local, the default: nothing leaves the machine.
python email_triage_standalone.py --owa

# Claude for screening.
ANTHROPIC_API_KEY=... EXTERNAL_AI_APPROVED=true \
  python email_triage_standalone.py --owa --provider anthropic --model claude-sonnet-4-5

# ChatGPT for screening, local Ollama for the sorting agent.
OPENAI_API_KEY=... EXTERNAL_AI_APPROVED=true \
  python email_triage_standalone.py --owa --provider openai --agent-provider ollama

# Any OpenAI-compatible gateway (OpenCode, LM Studio, a self-hosted router).
python email_triage_standalone.py --owa --provider custom \
  --base-url http://127.0.0.1:4096/v1 --model my-model
```

| Setting | Environment variable | Flag |
| --- | --- | --- |
| Screening provider | `TRIAGE_PROVIDER` (alias `TRIAGE_BACKEND`) | `--provider` |
| Screening model | `TRIAGE_MODEL`, or `<VENDOR>_MODEL` | `--model` |
| Screening endpoint | `TRIAGE_BASE_URL`, or `<VENDOR>_BASE_URL` / `OLLAMA_HOST` | `--base-url` |
| Screening key | `TRIAGE_API_KEY`, or the vendor variable (`ANTHROPIC_API_KEY`, …) | — |
| Sorting-agent provider | `TRIAGE_AGENT_PROVIDER` | `--agent-provider` |
| Sorting-agent model | `TRIAGE_AGENT_MODEL` | `--agent-model` |
| Sampling temperature | `TRIAGE_TEMPERATURE` | — |
| Request timeout, tool rounds | `TRIAGE_REQUEST_TIMEOUT`, `TRIAGE_AGENT_MAX_ROUNDS` | — |

`src/email_triage/providers.py` translates one neutral request shape into each
vendor dialect: Ollama's `/api/chat`, OpenAI's `/responses`, Anthropic's
`/v1/messages`, and OpenAI-compatible `/chat/completions`. Screening always asks
for the same JSON schema, and the sorting agent's tool calls and tool results are
translated per provider, so routing behavior does not change with the vendor.

## The sorting agent, and what bounds it

`src/email_triage/agent.py` runs a real tool-calling loop against the selected provider. It is deliberately fenced in:

- **It never sees the email body.** It receives only the validated screening result, plus the sender address and a truncated subject explicitly labelled untrusted.
- **Its tool surface is computed per message.** A clinical or low-confidence message is offered only `tag_message` and `file_message`, and `file_message`'s folder enum contains just `AI Triage/Needs Review`. `draft_reply` is not offered at all unless screening produced an approved reply.
- **Reply text cannot be rewritten.** The draft body must match the screened `suggested_reply` verbatim; the agent decides only *whether* to create the draft.
- **Every call is re-validated after the fact** by the policy gate in `src/email_triage/actions.py`. Rejections are returned to the model as tool errors so it can correct itself.
- **Send, forward, and delete are not representable.** There is no such tool and no such Graph call, so no prompt in an email body can produce one.
- **If the agent stalls, misbehaves, or its provider is unreachable**, the deterministic plan derived from the screening result is used instead. The `plan_source` field on every record records which one ran.

## Preserved screening behavior

- Routes: `needs_review`, `needs_reply`, `no_reply`; urgency `urgent`/`soon`/`routine`; topics clinical, scheduling, administrative, education/research, other.
- Summary, priority score 1–5, action items, deadline, rationale, and a professional suggested reply only when required. Replies end with `Best,` / `Nick`.
- Calendar items are skipped; no-reply senders route deterministically to `no_reply`.
- Prompt injection in a body is intercepted locally and sent to manual review. Clinical interception also applies when screening is not local.
- Private and Confidential messages follow normal routing; sensitivity is not sent to the model.
- Attachment names and contents are never requested or transmitted — only a `hasAttachments` Boolean.
- Provider or schema failures fail closed to manual review.

`var/review_queue.jsonl` records metadata and the structured result, `var/applied_actions.jsonl` records every action planned or applied. Neither stores the message body. Files under `var/` are owner-only and Git-ignored.

## Optional Microsoft Entra setup

Skip this entire section when using the default credential-free Outlook Web path.

1. Create a single-tenant app registration in Microsoft Entra ID.
2. Enable public-client/device-code authentication.
3. Add the delegated Microsoft Graph permission you intend to use:
   - `Mail.Read` — screening and preview only.
   - `Mail.ReadWrite` — required for `--apply` (folders, categories, drafts, move, read state). **Do not add `Mail.Send`.**
4. Grant consent per institutional policy.
5. Record the Directory (tenant) ID and Application (client) ID. No client secret is needed.

Changing between read and read-write invalidates the cached token, so the device-code prompt appears again on the first `--apply` run. If Conditional Access blocks device code, use the organization's approved brokered or browser-based OAuth flow rather than requesting an exception.

Before sending mailbox content anywhere off-device (any hosted provider, or a non-loopback local endpoint), obtain institutional privacy/security approval. This repository does not itself establish compliance.

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate && python -m pip install -e .
```

The runtime uses only the Python standard library. For the default local path,
Ollama must be running:

```bash
ollama serve && ollama pull qwen3:8b
```

`qwen3:8b` is the default and fits an 18 GB Apple Silicon machine; the classifier falls back to the strongest installed tag if it is missing.

## Run

The default path uses a dedicated Edge profile and the user's normal Outlook Web
login. You do **not** need an Entra app, Graph credentials, `.eml` export, or
Windows COM/`pywin32`. Existing Edge windows are never closed.

One-time setup, then leave Edge open:

```bash
python3 -m pip install -e '.[owa]'
bash scripts/open_outlook_in_edge.sh
email-triage --owa
```

Sign in normally in the dedicated Outlook window if prompted. Edge persists its
login state in the owner-only `~/Library/Application Support/MailTriage/EdgeProfile`
directory, separate from result exports. The application
captures a first-party Outlook bearer only in memory, filters replayed cookies to
`outlook.office.com`, supplies `X-AnchorMailbox`, and bounds reauthentication to
one retry. It never creates its own raw-token or cookie file. The helper rejects
an already-occupied CDP port unless its endpoint fingerprint matches the
owner-only profile marker. A malicious process running as the same macOS user
remains inside the local trust boundary and could inspect that user's browser
processes or files; use a separate OS account if that threat matters. Add
`--apply --watch` only when mailbox organization is desired.

`--watch` keeps screening every 5 minutes with no further input. Replies are saved as **unsent drafts**. Nothing is sent.

Preview against synthetic mail, no Microsoft account needed:

```bash
email-triage --input samples/inbox.jsonl
```

Use the optional registered Graph backend instead:

```bash
export MS_TENANT_ID="your-tenant-id" MS_CLIENT_ID="your-client-id"
email-triage --source graph
```

Sort the mailbox for real — moves messages into `AI Triage/…`, applies categories, saves unsent reply drafts:

```bash
email-triage --apply
```

Also mark filed mail as read (never applied to `needs_review` messages):

```bash
email-triage --apply --mark-read
```

`--apply-ids-file PATH` applies a previously previewed subset (JSON `message_ids`, max 200, no duplicates) without re-screening; requires `--apply` and `--source owa` or `graph`. A missing ID or mailbox 404 fails that row and continues (exit `1` if any row failed). Session or other backend errors abort and exit `2`.

Other flags: `--no-agent` uses the deterministic plan only; `--include-previously-processed` re-screens message IDs already in the local state file.

Each processed message prints one JSON line containing the screening result, `plan_source`, and the action outcomes. Exit codes: `0` success (or a skipped run because another copy holds the lock), `1` at least one action failed, `2` configuration or Graph error.

Inspect backend readiness without reading mail, capturing a token, or contacting a
model:

```bash
email-triage --source owa --diagnose
```

To validate one real backend connection, opt in explicitly:

```bash
email-triage --source graph --live-probe
email-triage --source owa --live-probe
email-triage --source desktop --live-probe
```

The live probe never contacts a model or mutates the mailbox. Graph and OWA make
one `$top=1&$select=id` request, discard the HTTP response without parsing it,
and do not persist refreshed credentials. The desktop probe checks only that
Outlook has a front window through Accessibility; it does not read visible text.
Output is fixed, redacted JSON with no token, message ID, subject, body, account,
attachment, response body, or request URL. `--source` is required, the Edge
debugging endpoint must be loopback-only, and write/login/watch flags are rejected.
Python attachment uses detach-only cleanup; the helper launches only its dedicated
profile and never closes the user's existing browser windows.

For a second credential-free macOS option, `--source accessibility` adapts the
MIT reader patterns from `Arkya-AI/outlook-email-scanner`. It reads only
explicitly-unread rows currently visible in an already-open Outlook Inbox and is
metadata-only and preview-only: no row selection, activation, navigation,
scrolling, attachment inspection, or mailbox writes. It classifies only the
subject/sender/preview text already exposed in each visible row. This
experimental source recognizes a separately installed
`atomacos`; it is intentionally not a core dependency because that stale GPLv2
package has conflicting dependency constraints. The single-front-window
`--source desktop` adapter remains dependency-free.

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for pinned upstream commits,
license notices, and the explicit exclusion of the MAM-bypass repository.

On macOS, `email-triage --source desktop` can screen the title of the single
Outlook message opened in the frontmost window. This metadata-only adapter does
not enumerate the window's static text, click, scroll, enumerate mail, inspect
attachment names/content, or apply changes.

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
