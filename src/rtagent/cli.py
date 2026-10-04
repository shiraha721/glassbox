"""A small CLI for the existing local Web service and its newline JSON/RPC interface."""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import quote

from rtagent.client import AgentClient
from rtagent.domain import AppError
from rtagent.rpc import serve_rpc


def parser() -> argparse.ArgumentParser:
    """Expose explicit actions without creating an alternate agent execution path."""
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--url", default="http://127.0.0.1:8001", help="Existing local service URL.")
    commands = root.add_subparsers(dest="command")
    commands.add_parser("serve", help="Start the Web service on loopback port 8001.")
    commands.add_parser("rpc", help="Forward newline JSON-RPC 2.0; stdout is JSON only.")
    run = commands.add_parser("run", help="Submit a direct Run; Preview is optional.")
    run.add_argument("task", nargs="?")
    run.add_argument("--task-file", type=Path)
    run.add_argument("--session")
    run.add_argument("--project")
    run.add_argument("--preset", default="general-agent")
    run.add_argument("--skill", action="append", default=[])
    run.add_argument("--wait", action="store_true")
    run.add_argument("--timeout", type=float, default=600)
    run.add_argument("--json", action="store_true")
    for action in ("status", "cancel", "wait", "events", "approve", "resume"):
        command = commands.add_parser(action)
        command.add_argument("run_id")
        if action == "wait":
            command.add_argument("--timeout", type=float, default=600)
        if action == "events":
            command.add_argument("--after", type=int, default=0)
        if action == "approve":
            command.add_argument("approval_id")
            command.add_argument("--reject", action="store_true")
        if action == "resume":
            command.add_argument("--resolutions", type=Path, help="JSON recovery decision file.")
    commands.add_parser("sessions")
    for kind in ("preset", "skill"):
        package = commands.add_parser(kind)
        package.add_argument("action", choices=["export", "import"])
        package.add_argument("file", type=Path)
        package.add_argument("--id", help="Preset ID or Skill name for export.")
        if kind == "skill":
            package.add_argument("--project")
    return root


async def execute(args: argparse.Namespace) -> int:
    """Use the shared HTTP client for all commands except the service composition entry point."""
    async with AgentClient(args.url) as client:
        if args.command == "rpc":
            await serve_rpc(client)
            return 0
        if args.command == "run":
            task = args.task_file.read_text(encoding="utf-8") if args.task_file else args.task
            if task is None:
                task = sys.stdin.read(100001)
            view = await client.run(
                task,
                session_id=args.session,
                project_id=args.project,
                preset_id=args.preset,
                skills=args.skill,
            )
            if args.wait:
                print(
                    f"Run {view.id} · conversation {view.session_id}", file=sys.stderr, flush=True
                )
                view = await client.wait(view.id, args.timeout)
            if args.json:
                print(view.model_dump_json())
            else:
                print(f"{view.id} · {view.status} · conversation {view.session_id}")
                for message in view.messages:
                    if message.role == "assistant" and message.content:
                        print(message.content)
                if view.approval:
                    print("Approval ID: " + str(view.approval["id"]))
            return 0 if view.status not in {"failed", "cancelled"} else 1
        if args.command in {"preset", "skill"}:
            path = "/presets" if args.command == "preset" else "/workspace/skills"
            suffix = (
                "?project_id=" + quote(args.project, safe="")
                if args.command == "skill" and args.project
                else ""
            )
            if args.action == "export":
                if not args.id:
                    raise AppError("package_id", "Supply --id for package export.")
                body = await client.request(
                    "GET", path + "/" + quote(args.id, safe="") + "/export" + suffix
                )
                # Export never overwrites an unrelated local file.
                with args.file.open("x", encoding="utf-8") as output:
                    json.dump(body, output, ensure_ascii=False, indent=2)
                    output.write("\n")
                print(str(args.file.resolve()))
            else:
                if args.file.stat().st_size > 2 * 1024 * 1024:
                    raise AppError("package_size", "Package JSON exceeds 2 MiB.")
                print(
                    json.dumps(
                        await client.request(
                            "POST",
                            path + "/import" + suffix,
                            json.loads(args.file.read_text(encoding="utf-8")),
                        ),
                        ensure_ascii=False,
                    )
                )
            return 0
        if args.command == "sessions":
            boot = await client.request("GET", "/bootstrap")
            result = boot["sessions"]
        elif args.command == "status":
            result = (await client.get(args.run_id)).model_dump()
        elif args.command == "wait":
            result = (await client.wait(args.run_id, args.timeout)).model_dump()
        elif args.command == "events":
            result = await client.events(args.run_id, args.after)
        else:
            path = "/runs/" + quote(args.run_id, safe="")
            body = None
            if args.command == "approve":
                path += "/approvals/" + quote(args.approval_id, safe="")
                body = {"decision": "reject" if args.reject else "approve"}
            elif args.command == "resume":
                path += "/resume"
                body = {
                    "resolutions": (
                        json.loads(args.resolutions.read_text(encoding="utf-8"))
                        if args.resolutions
                        else {}
                    )
                }
            else:
                path += "/cancel"
            result = await client.request("POST", path, body)
        print(json.dumps(result, ensure_ascii=False))
        return 0


def main() -> None:
    """Run the service by default or an explicit client command with concise stderr failures."""
    # Machine JSON and pipe input use UTF-8 even when Windows' redirected console uses ANSI.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parser().parse_args()
    if args.command in {None, "serve"}:
        from rtagent.main import serve

        serve()
        return
    try:
        raise SystemExit(asyncio.run(execute(args)))
    except (AppError, OSError, ValueError) as exc:
        print(
            exc.message if isinstance(exc, AppError) else f"Command failed ({type(exc).__name__}).",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        print("Client detached. Use cancel to stop an active Run.", file=sys.stderr)
        raise SystemExit(130) from None
