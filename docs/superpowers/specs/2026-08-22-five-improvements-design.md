# Mail Triage: five ranked improvements

Date: 2026-08-22
Status: approved design; Slice 1 is the next implementation plan, Slices 2–5 stay queued

## Goal

Improve daily review, screening personalization, and Outlook-session reliability without relaxing Mail Triage’s permanent fences: no send, forward, delete, attachment download, or agent rewrite of reply text. Destinations stay under `AI Triage/`. Clinical, prompt-injection, and low-confidence routing still cannot be overridden by learning.

This is five independent slices, ranked. One implementation plan covers **Slice 1 only**. Slices 2–5 are specified here so later plans do not reopen product decisions.

## Non-goals

- Automatic sending, forwarding, deletion, or unsubscribe
- In-app editing of a suggested reply before the unsent draft is created
- Undo of mailbox moves (move back to Inbox / delete a draft Mail Triage created)
- Microsoft Graph onboarding in the Mac app
- Using the user’s default Edge profile
- Unattended Edge/Ollama launch from the Mac app
- Per-message or rich HTML signatures
- Teaching a `needs_reply` route or inventing reply text the screener did not produce
- Infinite history, restoring the in-memory Activity view, or writing message bodies to disk

## Architecture

The SwiftUI app remains the operator console. The Python engine remains the only process that talks to Outlook or a model.

**Screen and actuate are separate operations.**

1. **Preview** screens a batch, plans actions, persists records, and leaves the mailbox unchanged.
2. **Apply-selected** (Mac app) loads persisted records for operator-accepted IDs, re-validates the plan, and writes to Outlook. It does not fetch unread mail and does not call a model.

The seam is `review_queue.jsonl` in the configured output directory (default `var/`, Mac app output folder). Each line is the same JSON object already printed to stdout today: screening record plus `plan_source` plus `actions`. Message bodies are never stored.

| Unit | Job | Depends on |
| --- | --- | --- |
| Engine preview | Screen, plan, persist full record lines | Mailbox (read), model, queue file |
| Engine apply-selected | Actuate accepted IDs only | Queue file, mailbox (write), action log, policy gate |
| Results session | Selection, filters, merge, restore | Queue file, engine stdout |
| Operator profile | Global reply closing | Settings → `TRIAGE_REPLY_CLOSING` → screening validator |
| Teaching | This-message patch and optional domain memory | Preference file + queue line |
| Session coach | Edge readiness copy and run-blocking | Existing `--diagnose` and Outlook helper |

CLI `--apply` **without** an ID file keeps today’s launchd meaning: one shot that screens unread mail and files the whole batch. Scheduled Mac-app runs stay preview-only.

```
Preview  →  review_queue.jsonl  →  Results (checkboxes)
                                      ↓
                               apply-ids file
                                      ↓
Apply-selected  →  policy gate  →  Outlook writes + applied_actions.jsonl
```

## Slice 1 — Selective apply (implement first)

### Operator flow

1. Run **Preview**. Every row in Results is checked. Mailbox is unchanged.
2. Uncheck messages that should stay in Inbox.
3. **Apply Triage** is disabled when the source cannot apply, a run is in progress, or zero rows are checked.
4. Confirmation: applying *N* messages may move them, add categories, and save unsent drafts. Mail Triage never sends, forwards, or deletes. Unchecked mail stays in Inbox.
5. The engine actuates only the checked IDs. Results **merge**: applied rows show live action status; unchecked rows stay dry-run.

Select All / Select None mutates only the **filtered** list (`routeFilter` + search). Hidden rows keep their previous check state.

A later Preview in the same session **replaces** the on-screen list until Slice 3. Unapplied leftovers remain on disk in the queue. Until Slice 3, they are not shown after that replacement; they can still be applied only if the operator still has them selected in the current session.

Mac Apply **requires** a current Results list. The app does not run the combined “screen this unread batch and write it” path. If Results is empty, Apply stays disabled and the operator must Preview first.

### Engine contract

New flag: `--apply-ids-file PATH`, required together with `--apply`.

The file is JSON, owner-only (`0600`), written by the Mac app into the output directory immediately before the run:

```json
{"message_ids": ["id-1", "id-2"]}
```

Rules:

- `--apply-ids-file` without `--apply` is a configuration error (exit `2`).
- `--apply-ids-file` with `--watch` is a configuration error.
- `--apply-ids-file` is valid only with mailbox source `owa` or `graph`. Other sources are a configuration error.
- `--include-previously-processed` is ignored (no unread scan happens).
- Empty `message_ids` or a missing/unreadable file is a configuration error.
- More than 200 IDs is a configuration error (the absolute batch cap, not the current Settings stepper).
- IDs not present in the queue, or a policy failure, or a mailbox 404: that message is marked failed; the rest continue.
- Exit `1` if any message failed; `0` if all selected messages succeeded; `2` for configuration/backend errors. Unchecked messages are not failures.

Apply-selected **must not**:

- Call `unread_messages`
- Construct or call a classifier / sorting-agent model
- Re-screen bodies

It loads matching queue lines (latest line wins per `message_id`), rebuilds `MailboxAction` values from the stored plan, runs each action through the existing policy gate in `actions.py`, and executes via the current Graph/OWA actuator. Stored JSON is untrusted input: a failed re-validation is a per-message failure, not a skipped gate.

Action JSON does not store reply text (`MailboxAction.to_dict` only notes that a draft exists). A `draft_reply` action is reconstituted from `analysis.suggested_reply` on the loaded record, then policy-checked: draft body must match that field verbatim, and only `needs_reply` rows may draft. Apply-selected does not rewrite `review_queue.jsonl`; it appends `applied_actions.jsonl` only.

If a stored plan is missing (older queue lines, or a Slice 4 operator correction), rebuild with `default_plan(record, mark_read)` and validate that. Do not call the sorting agent. `classify()` and `agent.plan()` are not invoked.

### Queue format change

Preview currently appends `ReviewRecord.to_dict()` (no plan). Preview must append the **same payload printed to stdout**: record fields plus `plan_source` plus `actions` (dry-run statuses). `applied_actions.jsonl` stays the audit log it is today.

`processed_message_ids.json` still means “do not screen this ID again.” Preview writes it so the next preview does not re-bill the model. It does **not** mean the message was filed. Unchecked mail stays in Inbox and stays skipped on later previews until the operator uses “include previously processed.”

Launchd `--apply` with no ID file is unchanged: screen then file in one process, still using the processed-ID skip list.

### Mac app wiring

- `EngineCommandBuilder` adds `--apply` and `--apply-ids-file` when `runMode == .apply` and at least one ID is selected.
- Write the ID file with `0600` before launch; overwrite each Apply.
- After apply, parse stdout and **merge** by `message_id` into `results`. Do not assign `results = newRecords`.
- Confirmation copy uses the selected count, not `results.count`.
- Checkboxes live on the Results list. Default after a successful Preview: all new records selected. After Apply, uncheck every row whose merged actions include a non-dry-run `applied` status. The ID file must omit those IDs even if a checkbox is somehow still on. Re-applying an already-filed ID is not a supported happy path.

### Tests (Slice 1)

Python, synthetic only (no Microsoft, no model):

- Apply-selected actuates only listed IDs
- `classify()` and `agent.plan()` are not invoked (test double)
- Missing ID → failed row, others proceed
- Empty/malformed ID file → exit `2`
- Policy re-check rejects a tampered folder outside `AI Triage/`
- Queue latest-wins when duplicate IDs exist
- Preview queue lines include `plan_source` and `actions`
- Existing tests still prove send/forward/delete are unrepresentable

Swift:

- New preview selects all
- Select All / None honors the current filter
- Apply disabled when selection is empty or source is preview-only
- Confirmation uses selected count
- Apply merges action status and does not drop unchecked rows

Regenerate `email_triage_standalone.py` so the bundled CLI carries the new flag. `python tools/build_single_file.py --check` must pass.

## Slice 2 — Configurable sign-off (queued)

Suggested replies stop requiring the literal suffix `Best,\nNick`. The operator sets one **closing block** used for every `needs_reply` result.

- Settings → General, multiline field, default `Best,\nNick`.
- Mac app exports `TRIAGE_REPLY_CLOSING`. CLI/launchd may set the same variable.
- Bounds: trimmed; 1–120 characters; at most four lines; only newline as a control character; empty input uses the default. No HTML. Never read from a message body.
- Screening instructions tell the model to end `suggested_reply` with that exact closing.
- `ScreeningResult` validates `endswith(closing)` using the configured value. Tests pass the default so fixtures stay stable.
- Per-sender `reply_guidance` remains style-only and cannot change the required suffix.
- The sorting agent still cannot rewrite reply text. The unsent draft must match screened `suggested_reply` verbatim, including this closing.
- Changing the closing does not rewrite drafts already in Outlook.

Tests: custom closing accepted; mismatched suffix rejected; out-of-bounds closing rejected; Swift forwards the env var; standalone regen.

## Slice 3 — Reload last results (queued)

Results are backed by the on-disk queue, not only the last process’s stdout. No mailbox read, no model call.

- On launch, read `review_queue.jsonl`: latest line per `message_id`, newest last, cap 200 unique IDs.
- Overlay the latest `applied_actions.jsonl` row per ID so applied vs dry-run is visible.
- Skip malformed lines. Missing file → empty list, no error dialog.
- `lastRunDate` is the queue file’s modification time when records exist.
- Restored rows are **unchecked** (do not apply yesterday’s leftovers by accident).
- A fresh Preview still selects all records from **that** run.
- In-session: a new Preview **keeps** still-unapplied records that were not in the new batch (closes the Slice 1 replacement gap). Applied IDs that appear again are replaced by the new screening. Unapplied first, then the new batch, still capped at 200 unique IDs.
- Activity log stays session-only. Bodies stay off disk.

Tests: Swift parser latest-wins, cap, bad line skipped, applied overlay, unchecked restore, merge of leftover unapplied rows.

## Slice 4 — Teach this message + folder picker (queued)

Saving a correction updates the current record so the next Apply honors it, not only future mail from that domain.

- Allowed edits on this message: route `no_reply` or `needs_review`, and `target_folder`.
- Saving appends a latest-wins queue line (no body) and updates the in-memory row.
- That line **replaces** the stored preview plan: it omits `actions` (or sets `plan_source` to `operator_correction`) so Apply-selected takes the missing-plan path and uses `default_plan` from the patched record plus the policy gate. It still cannot invent `needs_reply` or reply text.
- Checkbox **Remember for this sender domain**, on by default, also writes the existing preference file (`no_reply` / `needs_review` / folder / reply guidance).
- Domain rules still never override clinical, prompt-injection, or low-confidence safeguards. If this row is safeguarded, route and folder controls are disabled and the caption says why.
- Folder control is a menu, not a free-text field: `AI Triage/Needs Review`, `Needs Reply`, `No Reply Needed`, `Newsletters`, `Read Later`, `Administrative`, plus any already-valid custom `AI Triage/…` paths from preferences or current results. **Other…** still accepts a typed path with the current validator (2–4 segments, first `AI Triage`, no `.` / `..`, existing character class). No mailbox-wide folder browser.

Tests: patched queue line latest-wins; safeguarded rows refuse route/folder changes; `Archive` rejected; apply-selected files to the patched folder; Swift default checkbox on.

## Slice 5 — Outlook session coach (queued)

First-run and morning recovery stay on Overview. No new window. Never auto-launch Edge, a live probe, or Ollama.

- Persist last redacted diagnostic (`available`, `code`, `detail`, timestamp) in UserDefaults. Show it on launch, then re-check. No tokens, cookies, or mail in this cache.
- When source is Outlook on the Web and diagnostic is not ready: disable Preview, Apply, and scheduled Mac previews. Primary action: **Open Outlook Session** (existing helper). Other sources unchanged.
- The helper still refuses an occupied CDP port that is not this app’s session.
- Map codes to operator language: session not running; unrecognized process on the debugging port; Edge not installed; sign-in not verified.
- If a run dies because the session dropped, the error alert offers Open Session rather than stderr alone.
- Until a live probe has succeeded once, Overview shows three steps: open the dedicated Edge session, verify sign-in, confirm the AI provider. After that, the status cards remain the working UI.

Tests: Swift diagnostic round-trip; OWA `canRun` false when unreadiness is cached; code-to-copy mapping; helper invocation unchanged.

## Error handling

| Situation | Behavior |
| --- | --- |
| Not authenticated / Edge down (after Slice 5, OWA) | Do not start a run; show coach CTA |
| Apply with empty selection | Button disabled |
| ID file unreadable or empty | Exit `2`, Mac shows configuration error |
| One of N selected IDs missing or 404 | That row `failed` with detail; others continue; process exit `1` |
| Tampered stored plan fails policy | That row `failed`; nothing written for that ID |
| Classifier failure on preview | Existing fail-closed `needs_review` / low confidence |
| Lock busy | Existing skip, exit `0` |
| Malformed queue line on restore | Skip the line |

Never surface raw bearer tokens, cookies, or message bodies in Activity, alerts, or logs.

## Testing strategy

- Python unit tests stay synthetic: no Microsoft, no live model.
- Swift tests cover selection, merge, restore, and env forwarding.
- Warnings-as-errors Mac build and `build_single_file.py --check` remain the bundle gates.
- Safety tests that forbid send/forward/delete stay required for every slice.

## Implementation order

1. Slice 1 — Selective apply (next plan, next PR)
2. Slice 2 — Configurable sign-off
3. Slice 3 — Reload last results
4. Slice 4 — Teach this message + folder picker
5. Slice 5 — Outlook session coach

Slice 3 may be pulled forward if Slice 1’s “preview replaces the list” limitation proves too sharp in daily use; it must not block shipping Slice 1.

## Success criteria (Slice 1)

- Operator can preview 10 messages, uncheck 3, confirm Apply, and only 7 are moved/categorized/drafted.
- Unchecked messages remain in Inbox.
- Apply does not call a model (verified by tests).
- Nothing is sent, forwarded, or deleted.
- CLI `--apply` without an ID file still files a whole batch for launchd.
