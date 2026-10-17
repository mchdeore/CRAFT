"""Local CLI steering; credential values are never command arguments."""

import argparse
import asyncio
import json
from pathlib import Path

from craft_fleet.core import LocalResearchFleet


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--config-dir", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add")
    add.add_argument("--provider", default="deepseek")
    add.add_argument("--topic", required=True)
    add.add_argument("--sources", type=Path, required=True)
    add.add_argument("--max-output-tokens", type=int, default=1024)
    add.add_argument("--deadline", type=float, default=60)
    commands.add_parser("list")
    commands.add_parser("run")
    commands.add_parser("pause")
    commands.add_parser("resume")
    cancel = commands.add_parser("cancel")
    cancel.add_argument("job", type=int)
    show = commands.add_parser("show")
    show.add_argument("job", type=int)
    args = parser.parse_args()
    fleet = LocalResearchFleet(
        args.database, json.loads(args.registry.read_text()), args.config_dir
    )
    if args.command == "add":
        job = fleet.queue.add(
            {
                "provider": args.provider,
                "topic": args.topic,
                "source_excerpts": json.loads(args.sources.read_text()),
                "max_output_tokens": args.max_output_tokens,
                "deadline": args.deadline,
            }
        )
        print(json.dumps({"job": job}))
    elif args.command == "run":
        asyncio.run(fleet.run_queue())
        print(json.dumps(fleet.queue.list()))
    elif args.command == "list":
        print(json.dumps(fleet.queue.list()))
    elif args.command == "show":
        result = fleet.queue.show(args.job)
        print(json.dumps(result))
    elif args.command == "pause":
        fleet.queue.pause()
    elif args.command == "resume":
        fleet.queue.resume()
    elif args.command == "cancel":
        print(json.dumps({"cancelled": fleet.queue.cancel(args.job)}))


if __name__ == "__main__":
    main()
