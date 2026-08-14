#!/usr/bin/env python3
"""Generate macOS launchd agents so triage runs on a schedule with no operator.

This writes plist files into dist/launchd/ and prints the commands to install them.
It deliberately does not install or load anything itself: adding a launch agent is
persistent system configuration, so the final step stays with you.

    python tools/make_launch_agent.py --apply --interval-minutes 15
"""

from __future__ import annotations

import argparse
import plistlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "dist" / "launchd"
DEFAULT_LABEL = "com.emailtriage.sort"
OLLAMA_LABEL = "com.emailtriage.ollama"


def triage_plist(
    label: str,
    python: Path,
    interval_seconds: int,
    arguments: list[str],
    log_dir: Path,
) -> dict:
    return {
        "Label": label,
        "ProgramArguments": [str(python), "-m", "email_triage.cli", *arguments],
        "WorkingDirectory": str(ROOT),
        "EnvironmentVariables": {
            "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "PYTHONPATH": str(ROOT / "src"),
        },
        "StartInterval": interval_seconds,
        "RunAtLoad": True,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "StandardOutPath": str(log_dir / "triage.out.log"),
        "StandardErrorPath": str(log_dir / "triage.err.log"),
    }


def ollama_plist(ollama_binary: str, log_dir: Path) -> dict:
    return {
        "Label": OLLAMA_LABEL,
        "ProgramArguments": [ollama_binary, "serve"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "StandardOutPath": str(log_dir / "ollama.out.log"),
        "StandardErrorPath": str(log_dir / "ollama.err.log"),
    }


def build_arguments(args: argparse.Namespace) -> list[str]:
    arguments = ["--non-interactive"]
    if args.env_file:
        arguments += ["--env-file", str(Path(args.env_file).expanduser())]
    if args.apply:
        arguments.append("--apply")
    if args.owa:
        arguments.append("--owa")
    if args.mark_read:
        arguments.append("--mark-read")
    if args.no_agent:
        arguments.append("--no-agent")
    return arguments


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default=DEFAULT_LABEL, help="launchd label for the triage job.")
    parser.add_argument(
        "--interval-minutes", type=int, default=15, help="Minutes between runs (default 15)."
    )
    parser.add_argument("--apply", action="store_true", help="Write to the mailbox on each run.")
    parser.add_argument(
        "--owa",
        action="store_true",
        help="Read Outlook on the web from the Edge tab (no Entra admin app).",
    )
    parser.add_argument("--mark-read", action="store_true", help="Mark filed mail read.")
    parser.add_argument("--no-agent", action="store_true", help="Use the deterministic plan only.")
    parser.add_argument(
        "--env-file",
        default="~/.config/email-triage/env",
        help="Owner-only KEY=VALUE file the scheduled run reads (default ~/.config/email-triage/env).",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Interpreter to run. Defaults to the one generating this file.",
    )
    parser.add_argument(
        "--ollama-binary",
        default="/usr/local/bin/ollama",
        help="Also generate a KeepAlive agent that keeps `ollama serve` running.",
    )
    parser.add_argument(
        "--skip-ollama-agent",
        action="store_true",
        help="Do not generate the Ollama agent (use it when the Ollama app starts at login).",
    )
    args = parser.parse_args(argv)

    if args.interval_minutes < 1:
        print("error: --interval-minutes must be at least 1", file=sys.stderr)
        return 2

    log_dir = ROOT / "var" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    triage_path = OUTPUT_DIR / f"{args.label}.plist"
    triage_path.write_bytes(
        plistlib.dumps(
            triage_plist(
                args.label,
                Path(args.python),
                args.interval_minutes * 60,
                build_arguments(args),
                log_dir,
            )
        )
    )
    written.append(triage_path)

    if not args.skip_ollama_agent:
        ollama_path = OUTPUT_DIR / f"{OLLAMA_LABEL}.plist"
        ollama_path.write_bytes(plistlib.dumps(ollama_plist(args.ollama_binary, log_dir)))
        written.append(ollama_path)

    print("Wrote:")
    for path in written:
        print(f"  {path}")
    print("\nBefore the first scheduled run:")
    if args.owa:
        print("  bash scripts/open_outlook_in_edge.sh")
        print("  Leave that Edge window open with Outlook signed in.")
    else:
        login = "  email-triage --login --apply" if args.apply else "  email-triage --login"
        print(login)
    print(f"\nCreate the configuration file the job reads (chmod 600 {args.env_file}):")
    print(f"  install -m 600 .env.example {args.env_file}")
    print("\nThen install and start the agents:")
    for path in written:
        print(f"  cp {path} ~/Library/LaunchAgents/{path.name}")
    for path in written:
        label = path.stem
        print(f"  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/{path.name}")
        print(f"  launchctl kickstart -p gui/$(id -u)/{label}")
    print(f"\nLogs: {log_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
