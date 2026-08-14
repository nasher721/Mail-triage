# Secure Outlook Email Triage (Python)

This is a review-only replacement for the blocked Power Automate AI Builder path. It reads unread Inbox messages through Microsoft Graph delegated OAuth, sends only eligible message bodies plus the `hasAttachments` Boolean to the OpenAI Responses API, validates a strict structured result, and writes a private local JSONL review queue.

It does **not** use an Outlook password. It also does not send, reply, forward, create drafts, move messages, apply Outlook categories, download attachments, or mark messages read.

## Security actions required first

Credentials pasted into a chat must be considered exposed. Before running this project:

1. Revoke the exposed OpenAI API key and create a new one.
2. Change the exposed institutional password using your employer's official account-recovery process. Notify the security team if policy requires it.
3. Do not copy either replacement secret into source code, Git, screenshots, tickets, or chat.
4. Obtain institutional privacy/security approval before sending mailbox content to an external AI API. If email may contain PHI, use only an institutionally approved service configuration and required agreement; this repository does not itself establish compliance.

The application refuses to start unless `EXTERNAL_AI_APPROVED=true` is explicitly configured.

## Why Microsoft Graph instead of the password example

The provided `Credentials(username, password)` approach is Basic Authentication. Exchange Online has disabled Basic Authentication, and modern Microsoft 365 access should use OAuth. This implementation uses Microsoft Graph with delegated `Mail.Read`, so the user completes Microsoft sign-in and any required multifactor authentication without giving the script a mailbox password.

If `exchangelib` is a hard requirement, it must also be configured for OAuth; do not restore password authentication. Microsoft Graph is used here because it is the supported Microsoft 365 API and keeps the permission surface explicit.

## Preserved workflow behavior

- Routes: `needs_review`, `needs_reply`, and `no_reply`.
- Urgency: `urgent`, `soon`, or `routine`.
- Topics: clinical, scheduling, administrative, education/research, or other.
- Output also includes a concise summary, priority score 1–5, action items, deadline, rationale, and a professional suggested reply only when required.
- Suggested replies end with `Best,` and `Nick` and are never sent automatically.
- Calendar items are skipped.
- No-reply senders are deterministically routed to `no_reply`.
- Obvious clinical/patient content and prompt injection are intercepted locally and placed in manual review without sending the body to OpenAI.
- Private and Confidential messages are included and follow the same routing rules; sensitivity is not sent to OpenAI.
- Attachment names and contents are never requested or transmitted.
- Provider or schema failures fail closed to manual review.

The local queue records message metadata and the structured screening result, but never the original body. Files under `var/` are created with owner-only permissions and ignored by Git.

## Microsoft Entra setup

An administrator or authorized app owner should:

1. Create a single-tenant app registration in Microsoft Entra ID.
2. Enable public-client/device-code authentication for the app.
3. Add Microsoft Graph delegated permission `Mail.Read` only.
4. Grant consent according to institutional policy.
5. Record the Directory (tenant) ID and Application (client) ID. No client secret is needed for the device-code public client.

The organization may block user consent or device-code authentication. In that case, an Entra administrator must approve the app or select another institutionally supported delegated OAuth flow.

Device-code authentication is intended here for an approved local CLI, not a shared or unattended server. If institutional Conditional Access blocks it, use the organization's approved brokered or browser-based OAuth flow rather than requesting a broad exception.

## Install

```bash
cd email_triage_python
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

The runtime uses only the Python standard library; no third-party package is needed for OAuth, Microsoft Graph, or the OpenAI HTTPS request.

Set configuration in the shell or a secret manager. The project intentionally does not load `.env` files automatically:

```bash
export MS_TENANT_ID="your-tenant-id"
export MS_CLIENT_ID="your-public-client-application-id"
export OPENAI_API_KEY="your-newly-rotated-key"
export OPENAI_MODEL="gpt-4o"
export EXTERNAL_AI_APPROVED="true"
```

Then run:

```bash
email-triage
```

On first use, the CLI prints Microsoft's device-code instructions. Subsequent runs use an owner-readable token cache under `var/`. Results are appended to `var/review_queue.jsonl`, and `var/processed_message_ids.json` prevents repeated processing while messages remain unread.

Use `--include-previously-processed` only for deliberate re-screening:

```bash
email-triage --include-previously-processed
```

## Output shape

Each JSONL record contains source metadata, the intended folder and category names, and an `analysis` object:

```json
{
  "summary": "A colleague asks to confirm availability for a meeting.",
  "priority_score": 3,
  "action_items": ["Confirm availability."],
  "route": "needs_reply",
  "response_required": true,
  "confidence": "high",
  "urgency": "soon",
  "deadline": "next Tuesday",
  "topic": "scheduling",
  "manual_review_reason": null,
  "rationale": "Direct scheduling request.",
  "suggested_reply": "Thanks for reaching out. Tuesday afternoon works for me.\n\nBest,\nNick"
}
```

## Test

Tests use synthetic messages and never call Microsoft or OpenAI:

```bash
python -m unittest discover -s tests -v
```

## Deliberate first-release boundary

This release produces recommendations only. Once synthetic and mailbox acceptance testing succeeds under institutional approval, a separate, explicit `--apply` mode can be designed for Outlook folders/categories or draft creation. Automatic sending should remain permanently out of scope.

## Official references

- [Microsoft: Basic authentication is disabled in Exchange Online](https://learn.microsoft.com/en-us/exchange/clients-and-mobile-in-exchange-online/deprecation-of-basic-authentication-exchange-online)
- [Microsoft Graph: list messages](https://learn.microsoft.com/en-us/graph/api/user-list-messages?view=graph-rest-1.0)
- [Microsoft identity platform: device authorization grant](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-device-code)
- [OpenAI: Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [OpenAI: GPT-4o model capabilities](https://developers.openai.com/api/docs/models/gpt-4o)
