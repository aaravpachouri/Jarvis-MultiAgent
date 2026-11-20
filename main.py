"""JARVIS Agent Network — standalone local entry point."""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from core import CapabilityRouter, TaskRequest, create_runtime
from core.env import load_project_env


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jarvis-network",
        description="Standalone, expandable JARVIS multi-agent network.",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("list", help="List registered agent identities and runtime status.")
    route_parser = subparsers.add_parser("route", help="Show which agents best match a request.")
    route_parser.add_argument("prompt", nargs="+", help="The task you want to route.")
    run_parser = subparsers.add_parser("run", help="Submit a text request through JARVIS, the main handler.")
    run_parser.add_argument("prompt", nargs="+", help="The task you want JARVIS to handle.")
    subparsers.add_parser("chat", help="Start an interactive text session with JARVIS.")
    voice_parser = subparsers.add_parser("voice", help="Use your microphone and hear JARVIS speak responses.")
    voice_parser.add_argument(
        "--no-wake-word", action="store_true",
        help="Handle every recognised phrase without requiring the word 'Jarvis'.",
    )
    voice_parser.add_argument(
        "--language", default=None,
        help="Speech-recognition language code (default: JARVIS_STT_LANGUAGE or en-IN).",
    )
    voice_parser.add_argument(
        "--listen-timeout", type=float, default=6.0,
        help="Seconds to wait for speech to begin (default: 6).",
    )
    voice_parser.add_argument(
        "--phrase-time-limit", type=float, default=15.0,
        help="Maximum seconds per spoken phrase (default: 15).",
    )
    return parser


def _read_prompt(input_fn=input, output_fn=print) -> str:
    """Read one request; use :multi then :end for pasted multiline tasks."""
    first = input_fn("You > ").strip()
    if first.casefold() not in {":multi", "/multi", "multi"}:
        return first
    output_fn("Multiline mode: paste/type the complete task, then enter :end on its own line.")
    lines: list[str] = []
    while True:
        try:
            line = input_fn("... ")
        except EOFError:
            break
        if line.strip().casefold() in {":end", "/end", "end"}:
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _make_runtime():
    project_root = Path(__file__).resolve().parent
    load_project_env(project_root)
    return create_runtime(project_root=project_root)


def list_agents(runtime) -> int:
    rows = runtime.list_agents()
    print(f"JARVIS AGENT REGISTRY — {len(rows)} identities\n")
    print(f"{'NAME':<18} {'AGENT ID':<24} {'STATUS':<13} CAPABILITIES")
    print("-" * 120)
    for row in rows:
        capabilities = ", ".join(row["capabilities"])
        print(f"{row['name']:<18} {row['agent_id']:<24} {row['status']:<13} {capabilities}")
    print("\nRegistered means the identity is known. Ready means its handler is executable.")
    return 0


def route_prompt(runtime, prompt: str) -> int:
    router = CapabilityRouter(runtime.registry)
    matches = router.route(
        prompt,
        limit=8,
        minimum_score=1.0,
        exclude=("voice.jarvis",),
        executable_ids=runtime.executable_agent_ids(),
    )
    if not matches:
        print("No agent matched confidently. No task was executed.")
        return 1
    print(f"Routing analysis for: {prompt}\n")
    for index, match in enumerate(matches, start=1):
        status = "READY" if match.executable else "PROFILE ONLY"
        terms = ", ".join(match.matched_terms) or "capability overlap"
        print(f"{index}. {match.name} ({match.agent_id}) — score {match.score:.2f} — {status}")
        print(f"   Matched: {terms}")
    print("\nThis command previews routing only; it does not execute specialist work.")
    return 0


async def run_prompt(runtime, prompt: str) -> int:
    result = await runtime.run_task(TaskRequest(
        prompt=prompt,
        target_agent_id="voice.jarvis",
        requested_by="user",
    ))
    output = result.output
    if output is not None:
        if isinstance(output, str):
            print(output)
        else:
            print(output)
    else:
        print(result.error or result.summary)
    print(f"\nTask status: {result.status.value} | Agent: {result.agent_id} | Task ID: {result.task_id}")
    return 0 if result.succeeded else 1


async def interactive_chat(runtime) -> int:
    print("JARVIS Agent Network — text interface")
    print("Type 'exit' or 'quit' to leave. For long pasted tasks, enter :multi, paste the whole request, then enter :end.")
    print("Speech input/output is available with 'python main.py voice'.\n")
    while True:
        try:
            prompt = _read_prompt()
        except (EOFError, KeyboardInterrupt):
            print("\nJARVIS session ended.")
            return 0
        if not prompt:
            continue
        if prompt.casefold() in {"exit", "quit", "/exit", "/quit"}:
            print("JARVIS session ended.")
            return 0
        await run_prompt(runtime, prompt)
        print()


async def interactive_voice(runtime, args) -> int:
    from agents.voice_agent.audio_interface import VoiceIO
    from agents.voice_agent.session import run_voice_session

    try:
        voice_io = VoiceIO(
            language=args.language,
            listen_timeout=args.listen_timeout,
            phrase_time_limit=args.phrase_time_limit,
        )
    except (TypeError, ValueError) as exc:
        print(f"Invalid voice configuration: {exc}")
        return 2
    return await run_voice_session(
        runtime,
        voice_io=voice_io,
        use_wake_word=not args.no_wake_word,
        language=args.language,
    )


def _configure_console_encoding() -> None:
    """Prevent Windows redirected output from crashing on Unicode report text.

    Direct console sessions can keep their native encoding. For redirected output,
    prefer UTF-8 when the stream supports reconfiguration; PowerShell wrappers also
    set their native-output decoder to UTF-8 before invoking this process.
    """
    stdout = getattr(sys, "stdout", None)
    reconfigure = getattr(stdout, "reconfigure", None)
    if callable(reconfigure):
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            # Some embedded/test streams do not allow encoding changes.
            pass


def main() -> int:
    _configure_console_encoding()
    args = _build_parser().parse_args()
    try:
        runtime = _make_runtime()
    except Exception as exc:
        print(f"Could not initialise the agent network: {type(exc).__name__}: {exc}")
        return 2

    if args.command == "list":
        return list_agents(runtime)
    if args.command == "route":
        return route_prompt(runtime, " ".join(args.prompt))
    if args.command == "run":
        return asyncio.run(run_prompt(runtime, " ".join(args.prompt)))
    if args.command == "voice":
        try:
            return asyncio.run(interactive_voice(runtime, args))
        except KeyboardInterrupt:
            print("\nJARVIS voice session stopped.")
            return 0
    if args.command in (None, "chat"):
        return asyncio.run(interactive_chat(runtime))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
