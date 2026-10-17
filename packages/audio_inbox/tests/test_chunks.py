import hashlib
import wave
from pathlib import Path

import pytest

from audio_inbox.chunks import AudioChunk, merge_transcripts, pcm_chunks
from audio_inbox.models import AssetRef, Segment, TranscriptReply


def _mk_wav(path: Path, frames: int = 8000, rate: int = 8000) -> AssetRef:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x01" * frames)
    data = path.read_bytes()
    return AssetRef(
        id="src",
        path=path,
        sha256=hashlib.sha256(data).hexdigest(),
        media_type="audio/wav",
        duration=frames / rate,
        size_bytes=len(data),
    )


def test_pcm_chunks_offsets_size_and_cleanup(tmp_path: Path):
    src = _mk_wav(tmp_path / "a.wav", frames=16000, rate=8000)
    before_hash = src.sha256
    with pcm_chunks(
        src, window_seconds=1.0, overlap_seconds=0.02825, max_chunk_bytes=4096
    ) as chunks:
        assert len(chunks) >= 2
        assert chunks[0].offset_seconds == 0.0
        assert abs(chunks[1].offset_seconds - 0.225) < 1e-9  # reduced window to fit cap
        for c in chunks:
            assert c.asset.path.exists()
            assert c.asset.size_bytes <= 4096
            assert c.asset.path.read_bytes()[:4] == b"RIFF"
            assert c.start_frame < c.end_frame
        generated_paths = [c.asset.path for c in chunks]
    assert all(not p.exists() for p in generated_paths)
    assert src.path.exists()
    assert hashlib.sha256(src.path.read_bytes()).hexdigest() == before_hash


def test_pcm_chunks_reject_symlink_and_bad_options(tmp_path: Path):
    src = _mk_wav(tmp_path / "real.wav")
    link = tmp_path / "ln.wav"
    link.symlink_to(src.path)
    linked = AssetRef(
        id="ln",
        path=link,
        sha256=src.sha256,
        media_type="audio/wav",
        duration=src.duration,
        size_bytes=src.size_bytes,
    )
    with pytest.raises(ValueError):
        with pcm_chunks(linked):
            pass
    with pytest.raises(ValueError):
        with pcm_chunks(src, max_chunks=True):
            pass


def test_merge_transcripts_unknown_times_overlap_and_dedup(tmp_path: Path):
    base = _mk_wav(tmp_path / "b.wav")
    c1 = AudioChunk(base, 0, 100, 100, 0.0)
    c2 = AudioChunk(base, 80, 180, 100, 0.8)
    r1 = TranscriptReply(
        model="m",
        text="hello",
        segments=[Segment("s1", "hello", None, None, "A"), Segment("s2", "same", 0.8, 1.0)],
    )
    r2 = TranscriptReply(
        model="m",
        text="world",
        segments=[Segment("s3", "same", 0.0, 0.2), Segment("s4", "other", 0.3, 0.6, "B")],
    )
    merged = merge_transcripts((c1, c2), (r1, r2))
    assert merged.overlap_requires_review is True
    assert merged.reply.segments[0].start is None and merged.reply.segments[0].end is None
    assert merged.reply.segments[0].speaker == "A"
    # exact duplicate text+global interval is removed
    texts = [s.text for s in merged.reply.segments]
    assert texts.count("same") == 1


def test_merge_transcripts_mismatch_and_time_bounds(tmp_path: Path):
    base = _mk_wav(tmp_path / "c.wav")
    c = AudioChunk(base, 0, 100, 100, 0.0)
    ok = TranscriptReply("m1", "t", [Segment("x", "x", 0.1, 0.2)])
    bad_model = TranscriptReply("m2", "t", [Segment("y", "y", 0.1, 0.2)])
    with pytest.raises(ValueError):
        merge_transcripts((c, c), (ok, bad_model))
    bad_time = TranscriptReply("m1", "t", [Segment("z", "z", 0.0, 2.0)])
    with pytest.raises(ValueError):
        merge_transcripts((c,), (bad_time,))


def test_unknown_timestamp_text_is_not_silently_deduplicated(tmp_path):
    asset = _mk_wav(tmp_path / "unknown.wav")
    parts = (AudioChunk(asset, 0, 100, 100, 0), AudioChunk(asset, 80, 180, 100, 0.8))
    replies = (
        TranscriptReply("m", "same", [Segment("s1", "same")]),
        TranscriptReply("m", "same", [Segment("s2", "same")]),
    )
    result = merge_transcripts(parts, replies)
    assert len(result.reply.segments) == 2 and result.overlap_requires_review
    assert all(segment.start is None and segment.end is None for segment in result.reply.segments)


def test_generated_chunks_clean_up_after_caller_failure(tmp_path):
    asset = _mk_wav(tmp_path / "cleanup.wav")
    generated = []
    with pytest.raises(RuntimeError):
        with pcm_chunks(asset, window_seconds=0.25, overlap_seconds=0) as parts:
            generated = [part.asset.path for part in parts]
            raise RuntimeError("fixture failure")
    assert generated and all(not path.exists() for path in generated)
    assert asset.path.exists()


def test_bool_timestamp_and_malformed_decoded_frames_are_rejected(tmp_path):
    asset = _mk_wav(tmp_path / "bad.wav")
    with pytest.raises(ValueError):
        merge_transcripts(
            (AudioChunk(asset, 0, 100, 100, 0),),
            (TranscriptReply("m", "x", [Segment("s", "x", True, 1)]),),
        )
    data = asset.path.read_bytes()[:-2]
    asset.path.write_bytes(data)
    from dataclasses import replace

    broken = replace(asset, sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))
    with pytest.raises(ValueError):
        with pcm_chunks(broken):
            pass
