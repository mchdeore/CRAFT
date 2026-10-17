import os
import time
import wave
from typing import Any

import pytest

from audio_inbox.core import AudioInbox, Citation, GoalCandidate, Segment, TranscriptReply


def prepared(tmp_path, suggester=lambda version: []):
    drop = tmp_path / "drop"
    drop.mkdir()
    with wave.open(str(drop / "source.wav"), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(b"\0\0" * 16000)

    def transcriber(asset):
        return TranscriptReply(
            "fixture", "A report is due.", [Segment("s1", "A report is due.", 0, 1)]
        )

    box = AudioInbox(
        root=tmp_path / "store",
        inbox=drop,
        transcriber=transcriber,
        suggester=suggester,
        clock=lambda: time.time() + 10,
    )
    version = box.process(box.scan()[0])
    return box, drop, version, transcriber


def test_dead_suggestion_owner_is_uncertain_and_not_replayed(tmp_path):
    box, drop, version, transcriber = prepared(tmp_path)
    with box._connect() as db:
        db.execute(
            "INSERT INTO "
            "suggestion_runs(version_id,status,updated_at,owner_pid,claim_token) "
            "VALUES(?,'running',?,999999,'old')",
            (version, time.time()),
        )
    second = AudioInbox(
        root=box.root,
        inbox=drop,
        transcriber=transcriber,
        suggester=lambda version: pytest.fail("must not repeat"),
    )
    row = second._suggestion_run(version)
    assert row is not None and row["status"] == "uncertain"
    with pytest.raises(RuntimeError):
        second.proposals(version)
    assert second._proposal_rows(version) == []


def test_live_suggestion_owner_is_not_stolen(tmp_path):
    box, drop, version, transcriber = prepared(tmp_path)
    with box._connect() as db:
        db.execute(
            "INSERT INTO "
            "suggestion_runs(version_id,status,updated_at,owner_pid,claim_token) "
            "VALUES(?,'running',?,?,'live')",
            (version, time.time(), os.getpid()),
        )
    second = AudioInbox(
        root=box.root,
        inbox=drop,
        transcriber=transcriber,
        suggester=lambda version: pytest.fail("duplicate"),
    )
    row = second._suggestion_run(version)
    assert row is not None and row["status"] == "running"
    with pytest.raises(RuntimeError):
        second.proposals(version)


def test_stale_suggestion_completion_does_not_overwrite_new_claim(tmp_path):
    box, drop, version, transcriber = prepared(tmp_path)

    def changed(transcript):
        with box._connect() as db:
            db.execute(
                "UPDATE suggestion_runs SET claim_token='new-owner' WHERE version_id=?", (version,)
            )
        return [GoalCandidate("Report", "A report is due.", [Citation("s1", "report", 0, 1)])]

    box.suggester = changed
    with pytest.raises(RuntimeError, match="claim_lost"):
        box.proposals(version)
    assert box._proposal_rows(version) == []
    row = box._suggestion_run(version)
    assert row is not None and row["claim_token"] == "new-owner"


def test_integer_limits_reject_fractional_values(tmp_path):
    for field in ("max_file_bytes", "max_total_bytes", "max_text_chars", "max_safe_retries"):
        with pytest.raises(ValueError):
            AudioInbox(
                root=tmp_path / field,
                inbox=tmp_path / "drop",
                transcriber=lambda asset: TranscriptReply("fixture", "", []),
                suggester=lambda version: [],
                **dict[str, Any]({field: 1.5}),
            )
