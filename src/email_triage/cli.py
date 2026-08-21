from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from email_triage.actions import default_plan
from email_triage.accessibility import (
    OutlookAccessibilityMailbox,
    accessibility_diagnostic,
)
from email_triage.agent import DeterministicSortingAgent, SortingAgent, build_agent
from email_triage.apply import ActionLog, DryRunActuator, GraphActuator, apply_plan
from email_triage.backends import backend_capabilities
from email_triage.classifier import build_classifier
from email_triage.config import ConfigurationError, Settings
from email_triage.feedback import FeedbackPreferences
from email_triage.providers import PROVIDER_NAMES, describe_providers
from email_triage.desktop import OutlookDesktopMailbox, desktop_diagnostic
from email_triage.graph import GraphError, GraphMailbox
from email_triage.local_mailbox import LocalMailbox
from email_triage.live_probe import run_live_probe
from email_triage.owa import OwaMailbox, edge_debug_available
from email_triage.pipeline import LocalQueue, process_message
from email_triage.runtime import LockBusy, is_interactive, load_env_file, single_instance_lock


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Screen unread Outlook email with a local model, then sort it into triage "
            "folders with unsent reply drafts. Mail is never sent."
        )
    )
    parser.add_argument(
        "--input",
        help="Local JSONL, JSON, .eml file, or directory. Skips Microsoft Graph.",
    )
    parser.add_argument(
        "--owa",
        action="store_true",
        help=(
            "Read the Outlook tab already open in Microsoft Edge. No Entra admin app "
            "and no .eml export. Edge must be started with remote debugging "
            "(scripts/open_outlook_in_edge.sh)."
        ),
    )
    parser.add_argument(
        "--source",
        choices=("graph", "local", "owa", "desktop", "accessibility"),
        help="Mailbox source. --owa is the same as --source owa.",
    )
    parser.add_argument(
        "--provider",
        choices=PROVIDER_NAMES,
        help=(
            "AI system used for screening: ollama (default, local), openai, anthropic, "
            "openrouter, opencode, lmstudio, gemini, groq, and more. Overrides TRIAGE_PROVIDER."
        ),
    )
    parser.add_argument(
        "--model",
        help="Model id for the screening provider. Overrides TRIAGE_MODEL.",
    )
    parser.add_argument(
        "--base-url",
        help=(
            "Base URL for the screening provider. Needed for self-hosted gateways, "
            "Azure OpenAI deployments, and --provider custom."
        ),
    )
    parser.add_argument(
        "--agent-provider",
        choices=PROVIDER_NAMES,
        help="Run the sorting agent on a different AI system than screening.",
    )
    parser.add_argument(
        "--agent-model",
        help="Model id for the sorting agent when it differs from the screening model.",
    )
    parser.add_argument(
        "--list-providers",
        action="store_true",
        help=(
            "Print the supported AI systems with their default endpoints and key "
            "variables as JSON, then exit. Nothing is contacted."
        ),
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help=(
            "Print a metadata-only backend capability/readiness report and exit. "
            "No mailbox content is read and no model is contacted."
        ),
    )
    parser.add_argument(
        "--live-probe",
        action="store_true",
        help=(
            "Opt in to one real, read-only backend request. No model is contacted, "
            "no mailbox data is printed or retained, and apply mode is never used."
        ),
    )
    parser.add_argument(
        "--watch",
        nargs="?",
        type=int,
        const=300,
        default=None,
        metavar="SECONDS",
        help=(
            "Keep screening on a loop (default 300 seconds). Use this so triage continues "
            "while Outlook stays open in Edge. Bad configuration still exits instead of looping."
        ),
    )
    parser.add_argument(
        "--include-previously-processed",
        action="store_true",
        help="Reprocess message IDs already present in the local state file.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Write to the mailbox: move messages into AI Triage folders, apply categories, "
            "and save unsent reply drafts. Without this flag the plan is only previewed."
        ),
    )
    parser.add_argument(
        "--mark-read",
        action="store_true",
        help="Also mark filed messages as read (never for needs_review messages).",
    )
    parser.add_argument(
        "--no-agent",
        action="store_true",
        help="Skip the local sorting agent and use the deterministic plan.",
    )
    parser.add_argument(
        "--env-file",
        help=(
            "Read KEY=VALUE configuration from an owner-only file before running. "
            "Required for scheduled runs, which inherit no shell environment."
        ),
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help=(
            "Complete the Microsoft device-code sign-in and exit. Run once from a terminal; "
            "scheduled runs then refresh the cached token silently. Not used with --owa."
        ),
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help=(
            "Never prompt for sign-in; fail fast instead. Enabled automatically when "
            "standard input is not a terminal."
        ),
    )
    return parser


def _cli_source(args: argparse.Namespace) -> str | None:
    if args.owa:
        return "owa"
    return args.source


def _ai_report(args: argparse.Namespace) -> dict[str, object]:
    """Describe the selected AI systems without contacting them."""

    requested = args.provider or os.getenv("TRIAGE_PROVIDER", "") or os.getenv(
        "TRIAGE_BACKEND", ""
    ) or "ollama"
    try:
        settings = Settings.from_env(
            input_path=args.input,
            use_agent=False if args.no_agent else None,
            source=_cli_source(args),
            provider=args.provider,
            model=args.model,
            base_url=args.base_url,
            agent_provider=args.agent_provider,
            agent_model=args.agent_model,
        )
    except ConfigurationError as exc:
        return {"configured": False, "provider": requested.strip().lower(), "detail": str(exc)}
    return {
        "configured": True,
        "screening": settings.screening.to_dict(),
        "sorting_agent": (
            settings.agent.to_dict() if settings.use_agent else {"provider": "none"}
        ),
        "keeps_data_local": settings.uses_local_inference,
    }


def _diagnostic_report(args: argparse.Namespace) -> dict[str, object]:
    """Check backend readiness without reading mail, tokens, or model state."""

    source = _cli_source(args) or os.getenv("TRIAGE_SOURCE", "").strip().lower()
    if not source:
        graph_configured = bool(os.getenv("MS_TENANT_ID", "").strip()) and bool(
            os.getenv("MS_CLIENT_ID", "").strip()
        )
        source = "local" if args.input else ("graph" if graph_configured else "owa")
    if source not in {"graph", "local", "owa", "desktop", "accessibility"}:
        raise ConfigurationError(
            "TRIAGE_SOURCE must be graph, local, owa, desktop, or accessibility"
        )
    report: dict[str, object] = {
        "capabilities": backend_capabilities(source).to_dict(),
        "ai": _ai_report(args),
    }
    if source == "owa":
        cdp_url = (
            os.getenv("EDGE_CDP_URL", "").strip()
            or os.getenv("OWA_CDP_URL", "").strip()
            or "http://127.0.0.1:9222"
        )
        available = edge_debug_available(cdp_url)
        report["readiness"] = {
            "available": available,
            "code": "ready" if available else "cdp_unreachable",
            "detail": "Edge debugging endpoint only; no session token or mail was read.",
        }
    elif source == "desktop":
        report["readiness"] = desktop_diagnostic().to_dict()
    elif source == "accessibility":
        report["readiness"] = accessibility_diagnostic().to_dict()
    elif source == "local":
        raw_path = args.input or os.getenv("TRIAGE_INPUT", "")
        path = Path(raw_path).expanduser() if raw_path else None
        available = path is not None and path.exists()
        report["readiness"] = {
            "available": available,
            "code": "ready" if available else "input_missing",
            "detail": "Checks only whether the selected local path exists.",
        }
    else:
        available = bool(os.getenv("MS_TENANT_ID", "").strip()) and bool(
            os.getenv("MS_CLIENT_ID", "").strip()
        )
        report["readiness"] = {
            "available": available,
            "code": "configured" if available else "credentials_missing",
            "detail": "Checks configuration presence only; no authentication was attempted.",
        }
    return report


def _build_graph_mailbox(settings: Settings, interactive: bool) -> GraphMailbox:
    return GraphMailbox(
        settings.tenant_id,
        settings.client_id,
        settings.output_dir / "oauth_token_cache.json",
        read_write=settings.apply_changes,
        interactive=interactive,
        max_scan_pages=settings.max_retrieval_pages,
    )


def run(args: argparse.Namespace) -> int:
    interactive = is_interactive() and not args.non_interactive
    settings = Settings.from_env(
        input_path=args.input,
        apply_changes=args.apply or None,
        mark_read=args.mark_read or None,
        use_agent=False if args.no_agent else None,
        source=_cli_source(args),
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        agent_provider=args.agent_provider,
        agent_model=args.agent_model,
    )

    if args.login:
        if settings.mailbox_source != "graph":
            raise ConfigurationError(
                "--login is only for the Entra Graph app. Outlook in Edge uses --owa "
                "and the already-signed-in browser tab instead."
            )
        mailbox = _build_graph_mailbox(settings, interactive=True)
        mailbox.access_token()
        scope = "read-write" if settings.apply_changes else "read-only"
        print(
            f"Signed in and cached a {scope} Microsoft token in "
            f"{settings.output_dir / 'oauth_token_cache.json'}.",
            file=sys.stderr,
        )
        return 0

    live_mailbox: GraphMailbox | OwaMailbox | None = None
    if settings.mailbox_source == "local":
        if settings.input_path is None:
            raise ConfigurationError("Local mailbox source is missing an input path")
        mailbox_source: (
            GraphMailbox
            | LocalMailbox
            | OwaMailbox
            | OutlookDesktopMailbox
            | OutlookAccessibilityMailbox
        ) = LocalMailbox(
            settings.input_path
        )
        print(f"Using local mailbox {settings.input_path}.", file=sys.stderr)
    elif settings.mailbox_source == "owa":
        live_mailbox = OwaMailbox(
            settings.owa_cdp_url,
            read_write=settings.apply_changes,
            max_scan_pages=settings.max_retrieval_pages,
        )
        mailbox_source = live_mailbox
        print(
            f"Using Outlook on the web in Edge at {settings.owa_cdp_url}. "
            "Mail is never sent.",
            file=sys.stderr,
        )
    elif settings.mailbox_source == "desktop":
        mailbox_source = OutlookDesktopMailbox()
        print(
            "Using one user-opened, frontmost Outlook message through macOS "
            "Accessibility. This adapter is read-only and never enumerates the mailbox.",
            file=sys.stderr,
        )
    elif settings.mailbox_source == "accessibility":
        mailbox_source = OutlookAccessibilityMailbox()
        print(
            "Using currently visible unread rows in the already-open Outlook Inbox "
            "through macOS Accessibility. This adapter is preview-only.",
            file=sys.stderr,
        )
    else:
        live_mailbox = _build_graph_mailbox(settings, interactive)
        mailbox_source = live_mailbox

    classifier = build_classifier(settings)
    ready, detail = classifier.client.reachable()
    if not ready:
        raise ConfigurationError(detail)
    location = (
        "this machine"
        if settings.screening.keeps_data_local
        else settings.screening.base_url
    )
    print(
        f"Screening with {classifier.client.profile.label} model {classifier.model} "
        f"at {location}.",
        file=sys.stderr,
    )

    agent: SortingAgent | DeterministicSortingAgent = build_agent(settings)
    if isinstance(agent, SortingAgent):
        agent_location = (
            "this machine" if settings.agent.keeps_data_local else settings.agent.base_url
        )
        print(
            f"Sorting agent: {agent.client.profile.label} model {agent.model} "
            f"at {agent_location}. The agent never sees message bodies.",
            file=sys.stderr,
        )

    actuator: GraphActuator | DryRunActuator
    if settings.apply_changes and live_mailbox is not None:
        actuator = GraphActuator(live_mailbox)
        print(
            "Apply mode: messages will be moved, categorized, and given unsent reply "
            "drafts. Nothing is sent, forwarded, or deleted.",
            file=sys.stderr,
        )
    else:
        actuator = DryRunActuator()
        print("Preview mode: no mailbox changes. Add --apply to write.", file=sys.stderr)

    queue = LocalQueue(settings.output_dir)
    action_log = ActionLog(settings.output_dir)
    preferences = FeedbackPreferences.from_path(settings.feedback_path)
    processed = 0
    skipped = 0
    failures = 0
    excluded = set() if args.include_previously_processed else queue.seen_ids
    for message in mailbox_source.unread_messages(
        settings.max_unread_messages, exclude_ids=excluded
    ):
        if queue.contains(message.id) and not args.include_previously_processed:
            skipped += 1
            continue
        record = process_message(
            message,
            classifier,
                settings.max_body_characters,
                intercept_clinical=settings.intercept_clinical,
                preferences=preferences,
            )
        if record is None:
            skipped += 1
            continue
        queue.append(record)

        plan, plan_source = agent.plan(record, settings.mark_read)
        if not plan:
            plan, plan_source = default_plan(record, settings.mark_read), "deterministic"
        applied = apply_plan(record, plan, actuator)
        action_log.append(record, plan_source, actuator.mode, applied)
        if any(item.status == "failed" for item in applied):
            failures += 1

        payload = record.to_dict()
        payload["plan_source"] = plan_source
        payload["actions"] = [item.to_dict() for item in applied]
        print(json.dumps(payload, ensure_ascii=False))
        processed += 1

    summary = (
        f"Completed screening: {processed} messages, {skipped} skipped, "
        f"{failures} with action failures."
    )
    if settings.apply_changes:
        summary += " Mailbox updated; no mail was sent, forwarded, or deleted."
        if failures:
            summary += " Inbox-zero filing is pending for messages with failed actions."
        else:
            summary += " Every screened message was filed out of Inbox."
    else:
        summary += " Preview only; the mailbox was not modified."
    print(summary, file=sys.stderr)
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.env_file:
            loaded = load_env_file(Path(args.env_file).expanduser())
            print(
                f"Loaded {len(loaded)} setting(s) from {args.env_file}.",
                file=sys.stderr,
            )
        if args.owa and args.input:
            print(
                "Ignoring --input; --owa reads the live Outlook tab in Edge.",
                file=sys.stderr,
            )
        if args.list_providers:
            print(json.dumps(describe_providers(), indent=2, sort_keys=True))
            return 0
        if args.diagnose and args.live_probe:
            raise ConfigurationError("--diagnose and --live-probe are mutually exclusive")
        if args.diagnose:
            print(json.dumps(_diagnostic_report(args), sort_keys=True))
            return 0
        if args.live_probe:
            if args.apply or args.mark_read or args.login or args.watch is not None:
                raise ConfigurationError(
                    "--live-probe cannot be combined with --apply, --mark-read, "
                    "--login, or --watch"
                )
            source = _cli_source(args)
            if source is None:
                raise ConfigurationError(
                    "--live-probe requires an explicit --source graph|owa|desktop"
                )
            if source == "local" or args.input:
                raise ConfigurationError(
                    "--live-probe supports only graph, owa, or desktop without --input"
                )
            result = run_live_probe(source)
            print(json.dumps(result.to_dict(), sort_keys=True))
            return 0 if result.available else 2
        output_dir = Path(os.getenv("TRIAGE_OUTPUT_DIR", "var")).expanduser()
        watch_seconds = args.watch
        if watch_seconds is not None and watch_seconds < 1:
            raise ConfigurationError("--watch interval must be at least 1 second")
        # Validate flags/env once so a bad config cannot spin forever under --watch.
        Settings.from_env(
            input_path=None if args.owa else args.input,
            apply_changes=args.apply or None,
            mark_read=args.mark_read or None,
            use_agent=False if args.no_agent else None,
            source=_cli_source(args),
            provider=args.provider,
            model=args.model,
            base_url=args.base_url,
            agent_provider=args.agent_provider,
            agent_model=args.agent_model,
        )
        while True:
            try:
                with single_instance_lock(output_dir / "triage.lock"):
                    code = run(args)
            except LockBusy as exc:
                print(f"Skipping this run: {exc}", file=sys.stderr)
                code = 0
            except (ConfigurationError, GraphError) as exc:
                print(f"error: {exc}", file=sys.stderr)
                if watch_seconds is None:
                    return 2
                code = 2
            if watch_seconds is None:
                return code
            time.sleep(watch_seconds)
    except (ConfigurationError, GraphError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
