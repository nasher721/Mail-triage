# Outlook integration roadmap

This document records the implemented Outlook architecture and the decisions
made after reviewing these repositories at pinned commits:

- `Arkya-AI/outlook-email-scanner` at
  `79cbd27645dbe74f9cd1b824d0324773e92c8c5d`.
- `weirdapps/outlook-access` at
  `33a771743ece6bc1057e17c70b5b606951c829f6`.
- `Seaturtle111501/outlook-admin-bypass` at
  `db0680b932253f8797b302b48a704f140216cbc0`.

## Primary decision: Graph credentials are optional

When `MS_TENANT_ID` and `MS_CLIENT_ID` are absent, Mail-triage now defaults to
the credential-free Outlook Web backend. Run:

```bash
python3 -m pip install -e '.[owa]'
bash scripts/open_outlook_in_edge.sh
email-triage --owa
```

The helper launches a separate, owner-only Edge profile under Application
Support on a loopback debugging port and never quits existing Edge windows. It
records the endpoint fingerprint in that profile and rejects an unknown process
already occupying the configured port. The user signs in through Microsoft's normal
first-party UI, including MFA and Conditional Access. Mail-triage captures the
approved Outlook request bearer in memory and replays it only to exact Microsoft
Graph or Outlook REST origins. No app registration, tenant ID, client ID, client
secret, or API key is required.

## Code adapted from `weirdapps/outlook-access`

The OWA implementation now directly adapts these MIT-licensed patterns:

- Dedicated, owner-only persistent browser profile rather than taking over the
  default browser profile.
- Request-level bearer capture from allowlisted Outlook/Graph endpoints.
- Outlook REST cookie filtering to the `outlook.office.com` domain.
- In-memory JWT claim decoding to construct `X-AnchorMailbox`.
- One-time 401 reauthentication and exact-host pagination validation.
- Detach-only Playwright teardown so attachment cannot close the user's browser.

Mail-triage intentionally does not adopt upstream session-file persistence,
sending, forwarding, attachment download, SharePoint access, or its broad
command surface. Raw bearer tokens and browser cookies are never written to
application-managed token files. Edge itself persists login state in its
dedicated local profile; processes running as the same macOS user are therefore
part of the local trust boundary.

## Code adapted from `Arkya-AI/outlook-email-scanner`

Two macOS preview-only sources are available:

```bash
email-triage --source desktop
email-triage --source accessibility
```

`desktop` reads only the title of one message the user already opened in the
frontmost Outlook window using dependency-free AppleScript Accessibility.

`accessibility` adapts the upstream Outlook 16.x tree traversal and visible
`AXTable` row parsing. It is more conservative than upstream: it inspects only
subject/sender/preview metadata already exposed for currently visible rows with
an explicit unread marker. It never selects a row, because Outlook can mark mail
read on selection, and it does not activate Outlook, navigate accounts or
folders, scroll, search, inspect attachment names/content, or export bodies.
It is read-only and rejects `--apply` and `--mark-read`.

The Accessibility implementation recognizes a separately installed `atomacos`
module, but Mail-triage does not declare or bundle it. That dependency is stale,
GPLv2-licensed, and has conflicting `pyautogui` constraints; making it a core
dependency would be an unjustified licensing and maintenance risk.

## Explicitly rejected repository

No code is used from `outlook-admin-bypass`. Its sole meaningful behavior is an
Android LSPosed/Xposed hook that makes Outlook report that organizational MAM
device management is unnecessary. That is policy circumvention, not a
credential-free mailbox adapter. It also targets a different platform and is
GPL-3.0 licensed. Mail-triage will not reproduce or operationalize it.

## Completed reliability and privacy backlog

- [x] Make credential-free OWA the default when Graph registration is absent.
- [x] Lock Graph and OWA requests and pagination to approved HTTPS origins.
- [x] Use metadata-first retrieval and fetch bodies only for eligible,
  unprocessed, non-calendar messages.
- [x] Bound retrieval with `MAX_RETRIEVAL_PAGES` and `MAX_UNREAD_MESSAGES`.
- [x] Skip processed IDs during metadata pagination.
- [x] Resolve folder paths segment-by-segment and reject ambiguous duplicates.
- [x] Retry rejected credentials at most once.
- [x] Capture Outlook REST cookies and `X-AnchorMailbox` only in memory.
- [x] Keep browser CDP loopback-only and use detach-only cleanup.
- [x] Expose metadata-only `--diagnose` and redacted `--live-probe` commands.
- [x] Add frontmost-message and bounded visible-Inbox Accessibility adapters.
- [x] Keep attachment content, bulk body export, persistent application token
  files, sending, forwarding, deletion, and administrator-policy bypass out of
  scope.
- [x] Cover package and standalone boundaries with synthetic regression tests.

## Live validation

Readiness checks do not require mail access:

```bash
email-triage --diagnose
```

Real probes are explicit:

```bash
email-triage --source graph --live-probe
email-triage --source owa --live-probe
email-triage --source desktop --live-probe
```

Graph and OWA probes issue one `$top=1&$select=id` request but never read or
decode its response body. Desktop probing checks only process/front-window
presence. Probe output is fixed, redacted JSON asserting that no mailbox data
was retained, no model was contacted, and the mailbox was not mutated.

## Operational limits

- `MAX_UNREAD_MESSAGES` defaults to `20` eligible messages per run.
- `MAX_RETRIEVAL_PAGES` defaults to `10` remote metadata pages.
- `MAX_BODY_CHARACTERS` defaults to `12000` characters passed to screening.

See `THIRD_PARTY_NOTICES.md` for upstream copyright and MIT notices.
