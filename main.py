"""CLI entry point for open_Agent_Team."""

from __future__ import annotations

import argparse
import atexit
import json
import os
import sys
import signal
from pathlib import Path

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

if __package__ in (None, ""):
    package_dir = Path(__file__).resolve().parent
    if sys.path:
        sys.path[0] = str(package_dir)
    else:
        sys.path.insert(0, str(package_dir))

from config import (
    load_and_init_config,
    OpenTeamsConfig,
    CONFIG_FILE_NAME,
    LEGACY_CONFIG_FILE_NAMES,
    PACKAGE_DIR,
    DEFAULT_WORKSPACE_BASE,
)
from agents.leader import TeamLeader


def _print_progress(info: dict):
    elapsed = int(info["elapsed"])
    mm, ss = divmod(elapsed, 60)
    completed = info["completed"]
    total = info["total"]
    active = info["active_agents"]
    print(f"\r  [{mm:02d}:{ss:02d}] {completed}/{total} tasks completed | {active} agents running", end="", flush=True)


def _stream_text(chunk: str):
    print(chunk, end="", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="open_Agent_Team",
        description="Multi-agent collaborative coding system",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help=f"Path to JSON config file (default: {CONFIG_FILE_NAME}, with legacy fallback)",
    )
    parser.add_argument(
        "--init-config",
        action="store_true",
        help="Generate a default config file and exit",
    )
    parser.add_argument(
        "--project-root",
        type=str,
        default=None,
        help=f"Project root directory (default container: {DEFAULT_WORKSPACE_BASE}; auto-creates proj_<timestamp>)",
    )
    parser.add_argument(
        "--team-name",
        type=str,
        default=None,
        help="Team name (default: 'default')",
    )
    parser.add_argument(
        "--provider",
        type=str,
        default=None,
        choices=["anthropic", "openai"],
        help="LLM provider: 'anthropic' or 'openai' (OpenAI-compatible)",
    )
    parser.add_argument(
        "--leader-model",
        type=str,
        default=None,
        help="Model for team leader",
    )
    parser.add_argument(
        "--teammate-model",
        type=str,
        default=None,
        help="Default model for teammates",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="API key (default: ANTHROPIC_API_KEY or OPENAI_API_KEY env var)",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default=None,
        help="API base URL override (for proxies or third-party providers)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="Max tokens per API call",
    )
    parser.add_argument(
        "--max-agent-turns",
        type=int,
        default=None,
        help="Max turns per agent",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="Sampling temperature",
    )
    parser.add_argument(
        "--no-sandbox",
        action="store_true",
        help="Disable sandbox validation for file and shell tools.",
    )
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="Disable streaming output for leader responses.",
    )
    parser.add_argument(
        "--token-budget",
        type=int,
        default=None,
        help="Maximum cumulative token budget before a run is stopped.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=None,
        help="Maximum retry attempts for retryable LLM API errors.",
    )
    parser.add_argument(
        "-m", "--message",
        type=str,
        default=None,
        help="Single message mode: send one message and exit",
    )
    return parser.parse_args()


def generate_default_config(path: Path):
    """Write a default configuration file with comments."""
    config_template = {
        "provider": "anthropic",
        "api_key": "",
        "base_url": None,
        "leader_model": "claude-opus-4-6",
        "default_model": "claude-sonnet-4-6",
        "max_tokens": 16384,
        "max_turns": 200,
        "max_agent_turns": 50,
        "max_retries": 3,
        "max_context_tokens": 100000,
        "token_budget": 0,
        "temperature": 0.0,
        "sandbox_enabled": True,
        "sandbox_allowed_dirs": [],
        "streaming": True,
        "team_name": "default",
        "project_root": "workspace",
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config_template, f, indent=2, ensure_ascii=False)
    print(f"Default config written to: {path}")
    print()
    print("Edit the file to set your api_key and other options.")
    print("Provider options:")
    print('  "anthropic" — Anthropic Claude API (default)')
    print('  "openai"    — Any OpenAI-compatible API (OpenAI, DeepSeek, Qwen, vLLM, etc.)')
    print()
    print("For OpenAI-compatible providers, set:")
    print('  "provider":  "openai"')
    print('  "base_url":  "https://api.deepseek.com/v1"  (example)')
    print('  "leader_model": "deepseek-chat"              (example)')
    print('  "default_model": "deepseek-chat"             (example)')


def print_banner(config: OpenTeamsConfig):
    print("=" * 60)
    print("  open_Agent_Team: Multi-Agent Collaborative Coding System")
    print("=" * 60)
    print(f"  Provider: {config.provider}")
    print(f"  Leader:   {config.leader_model}")
    print(f"  Teammate: {config.default_model}")
    if config.base_url:
        print(f"  Base URL: {config.base_url}")
    print()
    print("Commands:")
    print("  /status  - Show team status")
    print("  /tasks   - Show task board")
    print("  /agents  - Show agent status")
    print("  /quit    - Exit")
    print()


def format_status(status: dict) -> str:
    lines = [f"Team: {status['team_name']}"]
    t = status["tasks"]
    lines.append(
        f"Tasks: {t['total']} total | {t['pending']} pending | "
        f"{t['in_progress']} in progress | {t['completed']} completed"
    )
    if status["agents"]:
        lines.append("Agents:")
        for name, st in status["agents"].items():
            lines.append(f"  - {name}: {st}")
    else:
        lines.append("Agents: none spawned yet")
    return "\n".join(lines)


def format_tasks(leader: TeamLeader) -> str:
    tasks = leader.task_board.list_tasks()
    if not tasks:
        return "No tasks on the board."
    lines = []
    for t in sorted(tasks, key=lambda x: int(x["id"])):
        icon = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}.get(
            t["status"], "[?]"
        )
        owner = t.get("owner") or "unassigned"
        lines.append(f"  #{t['id']} {icon} {t['subject']} ({owner})")
        if t.get("blockedBy"):
            lines.append(f"       blocked by: {', '.join(t['blockedBy'])}")
    return "\n".join(lines)


def main():
    args = parse_args()

    # --init-config: generate default config and exit
    if args.init_config:
        out_path = Path(args.config) if args.config else PACKAGE_DIR / CONFIG_FILE_NAME
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if out_path.exists():
            print(f"Config file already exists: {out_path}")
            print("Delete it first or specify a different path with --config.")
            sys.exit(1)
        generate_default_config(out_path)
        sys.exit(0)

    # Build CLI overrides dict (only non-None values)
    cli_overrides = {}
    if args.project_root:
        cli_overrides["project_root"] = args.project_root
    if args.team_name:
        cli_overrides["team_name"] = args.team_name
    if args.provider:
        cli_overrides["provider"] = args.provider
    if args.leader_model:
        cli_overrides["leader_model"] = args.leader_model
    if args.teammate_model:
        cli_overrides["default_model"] = args.teammate_model
    if args.api_key:
        cli_overrides["api_key"] = args.api_key
    if args.base_url:
        cli_overrides["base_url"] = args.base_url
    if args.max_tokens is not None:
        cli_overrides["max_tokens"] = args.max_tokens
    if args.max_agent_turns is not None:
        cli_overrides["max_agent_turns"] = args.max_agent_turns
    if args.temperature is not None:
        cli_overrides["temperature"] = args.temperature
    if args.no_sandbox:
        cli_overrides["sandbox_enabled"] = False
    if args.no_stream:
        cli_overrides["streaming"] = False
    if args.token_budget is not None:
        cli_overrides["token_budget"] = args.token_budget
    if args.max_retries is not None:
        cli_overrides["max_retries"] = args.max_retries

    # Load config: config file -> env vars -> CLI args (highest priority)
    config = load_and_init_config(
        config_file=args.config,
        cli_overrides=cli_overrides if cli_overrides else None,
    )

    if not config.api_key:
        print("Error: No API key configured.")
        print("Set it via one of:")
        known_paths = [PACKAGE_DIR / name for name in LEGACY_CONFIG_FILE_NAMES]
        print(f"  1. \"api_key\" in {known_paths[0]}")
        for extra_path in known_paths[1:]:
            print(f"     or legacy config file {extra_path}")
        print("  2. ANTHROPIC_API_KEY or OPENAI_API_KEY environment variable")
        print("  3. --api-key command-line flag")
        print()
        print("Run 'python main.py --init-config' or 'python -m main --init-config' to generate a config file.")
        sys.exit(1)

    leader = TeamLeader(config, team_name=config.team_name)
    atexit.register(leader.shutdown)

    def signal_handler(sig, frame):
        print("\nShutting down...")
        leader.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    # Single-message mode
    if args.message:
        if config.streaming:
            print("\nteam-lead> ", end="", flush=True)
            response = leader.handle_user_message(args.message, stream_callback=_stream_text)
            print("\n")
        else:
            response = leader.handle_user_message(args.message)
            print(f"\nteam-lead> {response}\n")
        if leader.agent_manager.active_count > 0:
            print("[Team working...]\n")
            all_completed = leader.wait_for_completion(timeout=1800, progress_callback=_print_progress)
            print()
            if all_completed:
                print("\n[All agents completed. Synthesizing results...]\n")
                if config.streaming:
                    print("team-lead> ", end="", flush=True)
                    synthesis = leader.synthesize_results(stream_callback=_stream_text)
                    print("\n")
                else:
                    synthesis = leader.synthesize_results()
                    print(f"\nteam-lead> {synthesis}\n")
            else:
                print("\n[Agent work stopped before all tasks completed. Use /tasks or /status to inspect remaining work.]\n")
        leader.shutdown()
        return

    # Interactive REPL
    print_banner(config)

    first_message = True
    while True:
        try:
            user_input = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nShutting down...")
            leader.shutdown()
            break

        if not user_input:
            continue

        if user_input.lower() == "/quit":
            print("Shutting down...")
            leader.shutdown()
            break

        if user_input.lower() == "/status":
            print(format_status(leader.get_team_status()))
            continue

        if user_input.lower() == "/tasks":
            print(format_tasks(leader))
            continue

        if user_input.lower() == "/agents":
            status = leader.agent_manager.get_status()
            if not status:
                print("No agents spawned yet.")
            else:
                for name, st in status.items():
                    print(f"  {name}: {st}")
            continue

        if user_input.lower() == "/config":
            print(json.dumps(config.to_dict(), indent=2, ensure_ascii=False))
            continue

        try:
            if first_message:
                if config.streaming:
                    print("\nteam-lead> ", end="", flush=True)
                    response = leader.handle_user_message(
                        user_input, stream_callback=_stream_text
                    )
                    print("\n")
                else:
                    response = leader.handle_user_message(user_input)
                    print(f"\nteam-lead> {response}\n")
                first_message = False
            else:
                if config.streaming:
                    print("\nteam-lead> ", end="", flush=True)
                    response = leader.handle_followup(
                        user_input, stream_callback=_stream_text
                    )
                    print("\n")
                else:
                    response = leader.handle_followup(user_input)
                    print(f"\nteam-lead> {response}\n")

            if leader.agent_manager.active_count > 0:
                print("[Team working... Ctrl+C to return to prompt]\n")
                old_handler = signal.getsignal(signal.SIGINT)
                try:
                    signal.signal(signal.SIGINT, signal.default_int_handler)
                    all_completed = leader.wait_for_completion(timeout=1800, progress_callback=_print_progress)
                    print()
                except KeyboardInterrupt:
                    print("\n\n[Agents still running. Use /status to check, or type a message.]\n")
                    signal.signal(signal.SIGINT, old_handler)
                    continue
                signal.signal(signal.SIGINT, old_handler)
                if all_completed:
                    print("\n[All agents completed. Synthesizing results...]\n")
                    if config.streaming:
                        print("team-lead> ", end="", flush=True)
                        synthesis = leader.synthesize_results(stream_callback=_stream_text)
                        print("\n")
                    else:
                        synthesis = leader.synthesize_results()
                        print(f"\nteam-lead> {synthesis}\n")
                else:
                    print("\n[Agent work stopped before all tasks completed. Use /tasks or /status to inspect remaining work.]\n")
        except Exception as e:
            print(f"\nError: {e}\n")


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
