from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from email_triage.actions import default_plan
from email_triage.agent import DeterministicSortingAgent, OllamaSortingAgent
from email_triage.apply import ActionLog, DryRunActuator, GraphActuator, apply_plan
from email_triage.classifier import _list_ollama_models, build_classifier, resolve_ollama_model
from email_triage.config import ConfigurationError, Settings
from email_triage.graph import GraphError, GraphMailbox
from email_triage.local_mailbox import LocalMailbox
from email_triage.owa import OwaMailbox
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
        choices=("graph", "local", "owa"),
        help="Mailbox source. --owa is the same as --source owa.",
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


def _build_graph_mailbox(settings: Settings, interactive: bool) -> GraphMailbox:
    return GraphMailbox(
        settings.tenant_id,
        settings.client_id,
        settings.output_dir / "oauth_token_cache.json",
        read_write=settings.apply_changes,
        interactive=interactive,
    )


def run(args: argparse.Namespace) -> int:
    interactive = is_interactive() and not args.non_interactive
    settings = Settings.from_env(
        input_path=args.input,
        apply_changes=args.apply or None,
        mark_read=args.mark_read or None,
        use_agent=False if args.no_agent else None,
        source=_cli_source(args),
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
        mailbox_source: GraphMailbox | LocalMailbox | OwaMailbox = LocalMailbox(
            settings.input_path
        )
        print(f"Using local mailbox {settings.input_path}.", file=sys.stderr)
    elif settings.mailbox_source == "owa":
        live_mailbox = OwaMailbox(settings.owa_cdp_url, read_write=settings.apply_changes)
        mailbox_source = live_mailbox
        print(
            f"Using Outlook on the web in Edge at {settings.owa_cdp_url}. "
            "Mail is never sent.",
            file=sys.stderr,
        )
    else:
        live_mailbox = _build_graph_mailbox(settings, interactive)
        mailbox_source = live_mailbox

    classifier = build_classifier(settings)
    if settings.ai_backend == "ollama":
        if not _list_ollama_models(settings.ollama_host):
            raise ConfigurationError(
                f"Ollama is unavailable at {settings.ollama_host} or has no models. "
                "Start it with `ollama serve` and run `ollama pull qwen3:8b` "
                "(best fit for 18GB Apple Silicon)."
            )
        location = "this machine" if settings.uses_local_inference else settings.ollama_host
        print(
            f"Using Ollama model {classifier.model} at {location}. "
            "Email bodies are not sent to OpenAI.",
            file=sys.stderr,
        )

    agent: OllamaSortingAgent | DeterministicSortingAgent
    if settings.use_agent:
        agent = OllamaSortingAgent(
            settings.ollama_host,
            resolve_ollama_model(settings.ollama_host, settings.ollama_model),
        )
    else:
        agent = DeterministicSortingAgent()

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
    processed = 0
    skipped = 0
    failures = 0
    for message in mailbox_source.unread_messages(settings.max_unread_messages):
        if queue.contains(message.id) and not args.include_previously_processed:
            skipped += 1
            continue
        record = process_message(
            message,
            classifier,
            settings.max_body_characters,
            intercept_clinical=settings.intercept_clinical,
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
