from __future__ import annotations

import argparse
import json
import sys

from email_triage.classifier import _list_ollama_models, build_classifier
from email_triage.config import ConfigurationError, Settings
from email_triage.graph import GraphError, GraphMailbox
from email_triage.local_mailbox import LocalMailbox
from email_triage.pipeline import LocalQueue, process_message


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Screen unread Outlook email into a local review queue."
    )
    parser.add_argument(
        "--input",
        help="Local JSONL, JSON, .eml file, or directory. Skips Microsoft Graph.",
    )
    parser.add_argument(
        "--include-previously-processed",
        action="store_true",
        help="Reprocess message IDs already present in the local state file.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = Settings.from_env(input_path=args.input)
        if settings.mailbox_source == "local":
            if settings.input_path is None:
                raise ConfigurationError("Local mailbox source is missing an input path")
            mailbox: GraphMailbox | LocalMailbox = LocalMailbox(settings.input_path)
            print(f"Using local mailbox {settings.input_path}.", file=sys.stderr)
        else:
            mailbox = GraphMailbox(
                settings.tenant_id,
                settings.client_id,
                settings.output_dir / "oauth_token_cache.json",
            )
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
        queue = LocalQueue(settings.output_dir)
        processed = 0
        skipped = 0
        for message in mailbox.unread_messages(settings.max_unread_messages):
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
            print(json.dumps(record.to_dict(), ensure_ascii=False))
            processed += 1
        print(
            f"Completed review-only screening: {processed} queued, {skipped} skipped. "
            "No email was sent, moved, categorized, or marked read.",
            file=sys.stderr,
        )
        return 0
    except (ConfigurationError, GraphError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
