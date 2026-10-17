from __future__ import annotations

import argparse
from pathlib import Path

from .core import AudioInbox, Citation, GoalCandidate, Segment, TranscriptReply


class _FakeTranscriber:
    def __call__(self, asset):
        end = min(1.0, max(0.1, float(asset.duration)))
        text = "Send the report Friday."
        return TranscriptReply(
            model="fake-transcriber",
            text=text,
            segments=[Segment(segment_id="s1", text=text, start=0.0, end=end)],
        )


class _FakeSuggester:
    def __call__(self, _transcript):
        return [
            GoalCandidate(
                title="Send the report",
                action="Send the report Friday.",
                citations=[
                    Citation(segment_id="s1", quote="Send the report Friday.", start=0.0, end=1.0)
                ],
            )
        ]


def _build(args: argparse.Namespace) -> AudioInbox:
    if args.providers is not None:
        if args.fake:
            raise SystemExit("Choose generated-data mode or configured providers")
        from .config import providers

        transcriber, suggester = providers(args.providers)
        if args.cmd == "process" and transcriber is None:
            raise SystemExit("Transcription service is not configured")
        if args.cmd == "suggest" and suggester is None:
            raise SystemExit("Goal model adapter is not configured")

        def unavailable(_):
            raise RuntimeError("provider_not_configured")

        return AudioInbox(
            root=args.store,
            inbox=args.inbox,
            transcriber=transcriber or unavailable,
            suggester=suggester or unavailable,
        )
    return AudioInbox(
        root=args.store,
        inbox=args.inbox,
        transcriber=_FakeTranscriber(),
        suggester=_FakeSuggester(),
    )


def main() -> int:
    p = argparse.ArgumentParser(prog="audio-inbox")
    p.add_argument("--fake", action="store_true", help="Enable generated-data fake processing")
    p.add_argument("--providers", type=Path, help="Private provider configuration file")
    p.add_argument("--store", type=Path, default=Path.home() / ".local" / "private" / "audio-inbox")
    p.add_argument("--inbox", type=Path, default=Path.home() / "Downloads" / "CRAFT Audio Inbox")
    sp = p.add_subparsers(dest="cmd", required=True)

    sp.add_parser("setup")
    sp.add_parser("scan")
    sp.add_parser("list")

    suggest = sp.add_parser("suggest")
    suggest.add_argument("version_id")

    proc = sp.add_parser("process")
    proc.add_argument("job_id")

    rev = sp.add_parser("review")
    rev.add_argument("proposal_id")
    rev.add_argument("decision", choices=["approve", "reject", "edit"])
    rev.add_argument("--title")
    rev.add_argument("--action")

    exp = sp.add_parser("export")
    exp.add_argument("version_id")
    exp.add_argument("destination", type=Path)

    cancel = sp.add_parser("cancel")
    cancel.add_argument("job_id")

    args = p.parse_args()
    if args.cmd == "setup":
        args.inbox.mkdir(parents=True, exist_ok=True)
        if hasattr(args, "store"):
            args.store.mkdir(parents=True, exist_ok=True)
        print(args.inbox)
        return 0

    box = _build(args)
    if args.cmd == "scan":
        for jid in box.scan():
            print(jid)
        return 0
    if args.cmd == "list":
        for job in box.jobs():
            print(
                f"{job.id} {job.source_name} {job.status} "
                f"asset={job.asset_id} version={job.version_id}"
            )
        return 0
    if args.cmd == "process":
        if not args.fake and args.providers is None:
            raise SystemExit(
                "Transcription service is not configured; --fake is for generated fixtures"
            )
        print(box.process(args.job_id))
        return 0
    if args.cmd == "suggest":
        if not args.fake and args.providers is None:
            raise SystemExit("Goal model adapter is not configured")
        for proposal in box.proposals(args.version_id):
            print(f"{proposal.id} {proposal.status}")
        return 0
    if args.cmd == "review":
        out = box.review(
            args.proposal_id, args.decision, edited_title=args.title, edited_action=args.action
        )
        print(f"{out.id} {out.status}")
        return 0
    if args.cmd == "export":
        print(box.export(args.version_id, args.destination))
        return 0
    if args.cmd == "cancel":
        box.cancel(args.job_id)
        print("cancelled")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
