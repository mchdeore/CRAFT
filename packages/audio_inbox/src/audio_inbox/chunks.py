from __future__ import annotations

import hashlib
import io
import math
import os
import wave
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from .models import AssetRef, Segment, TranscriptReply
from .providers import ProviderAdapterError, _verify_asset_snapshot
from .validation import validate_transcript

MIB = 1024 * 1024


@dataclass(frozen=True)
class AudioChunk:
    asset: AssetRef
    start_frame: int
    end_frame: int
    sample_rate: int
    offset_seconds: float


@dataclass(frozen=True)
class MergedTranscript:
    reply: TranscriptReply
    overlap_requires_review: bool


def _is_num(value: object) -> bool:
    return type(value) in (int, float) and not isinstance(value, bool)


def _as_int(name: str, value: object, minimum: int = 1) -> int:
    if type(value) is not int or isinstance(value, bool) or value < minimum:
        raise ValueError(f"invalid_{name}")
    return value


def _as_time(name: str, value: object, positive: bool = True) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"invalid_{name}")
    out = float(value)
    if not math.isfinite(out) or (out <= 0 if positive else out < 0):
        raise ValueError(f"invalid_{name}")
    return out


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@contextmanager
def pcm_chunks(
    asset: AssetRef,
    *,
    max_chunk_bytes: int = 20 * MIB,
    window_seconds: float = 60,
    overlap_seconds: float = 0.5,
    max_chunks: int = 64,
    max_spool_bytes: int = 150 * MIB,
) -> Iterator[tuple[AudioChunk, ...]]:
    max_chunk_bytes = _as_int("max_chunk_bytes", max_chunk_bytes)
    max_chunks = _as_int("max_chunks", max_chunks)
    max_spool_bytes = _as_int("max_spool_bytes", max_spool_bytes)
    window_seconds = _as_time("window_seconds", window_seconds, positive=True)
    overlap_seconds = _as_time("overlap_seconds", overlap_seconds, positive=False)
    if overlap_seconds >= window_seconds:
        raise ValueError("invalid_overlap")
    if max_chunk_bytes <= 44:
        raise ValueError("invalid_max_chunk_bytes")

    try:
        snapshot = _verify_asset_snapshot(asset, 100 * MIB)
    except ProviderAdapterError as error:
        raise ValueError(str(error)) from None
    with wave.open(io.BytesIO(snapshot), "rb") as src:
        if src.getcomptype() != "NONE":
            raise ValueError("unsupported_wav_encoding")
        channels = src.getnchannels()
        sample_width = src.getsampwidth()
        sample_rate = src.getframerate()
        total_frames = src.getnframes()
        if (
            not 1 <= channels <= 8
            or sample_width not in (1, 2, 3, 4)
            or not 1 <= sample_rate <= 192000
            or total_frames <= 0
        ):
            raise ValueError("malformed_wav")
        if not math.isclose(asset.duration, total_frames / sample_rate, abs_tol=1e-9):
            raise ValueError("source_duration_mismatch")
        frame_size = channels * sample_width
        cap_frames = (max_chunk_bytes - 44) // frame_size
        if cap_frames <= 0:
            raise ValueError("chunk_cap_too_small")

        req_window_frames = int(window_seconds * sample_rate)
        overlap_frames = int(overlap_seconds * sample_rate)
        if req_window_frames <= 0:
            raise ValueError("invalid_window_seconds")
        window_frames = min(req_window_frames, cap_frames)
        if overlap_frames >= window_frames:
            raise ValueError("invalid_overlap")
        step = window_frames - overlap_frames

        generated: list[Path] = []
        chunks: list[AudioChunk] = []
        spool_used = 0
        recipe = (
            f"{max_chunk_bytes}:{window_seconds}:{overlap_seconds}:{window_frames}:{overlap_frames}"
        )

        with TemporaryDirectory() as td:
            td_path = Path(td)
            os.chmod(td_path, 0o700)
            try:
                start = 0
                index = 0
                while start < total_frames:
                    if index >= max_chunks:
                        raise ValueError("max_chunks_exceeded")
                    end = min(start + window_frames, total_frames)
                    frames = end - start
                    src.setpos(start)
                    data = src.readframes(frames)
                    if len(data) != frames * frame_size:
                        raise ValueError("malformed_wav")

                    cid = hashlib.sha256(
                        f"{asset.sha256}:{start}:{end}:{recipe}".encode()
                    ).hexdigest()
                    out_path = td_path / f"{cid}.wav"
                    with wave.open(str(out_path), "wb") as dst:
                        dst.setnchannels(channels)
                        dst.setsampwidth(sample_width)
                        dst.setframerate(sample_rate)
                        dst.writeframes(data)
                    os.chmod(out_path, 0o600)
                    size = out_path.stat().st_size
                    if size > max_chunk_bytes:
                        raise ValueError("chunk_size_exceeded")
                    spool_used += size
                    if spool_used > max_spool_bytes:
                        raise ValueError("spool_size_exceeded")

                    chunk_asset = AssetRef(
                        id=cid,
                        path=out_path,
                        sha256=_sha256_file(out_path),
                        media_type="audio/wav",
                        duration=frames / sample_rate,
                        size_bytes=size,
                    )
                    chunks.append(
                        AudioChunk(
                            asset=chunk_asset,
                            start_frame=start,
                            end_frame=end,
                            sample_rate=sample_rate,
                            offset_seconds=start / sample_rate,
                        )
                    )
                    generated.append(out_path)
                    index += 1
                    if end >= total_frames:
                        break
                    start += step
                yield tuple(chunks)
            finally:
                for p in generated:
                    try:
                        p.unlink(missing_ok=True)
                    except OSError:
                        pass


def merge_transcripts(
    chunks: Iterable[AudioChunk],
    replies: Iterable[TranscriptReply],
    *,
    max_text_chars: int = 200_000,
) -> MergedTranscript:
    if type(max_text_chars) is not int or isinstance(max_text_chars, bool) or max_text_chars <= 0:
        raise ValueError("invalid_max_text_chars")

    ch = list(chunks)
    rp = list(replies)
    if not ch or len(ch) != len(rp):
        raise ValueError("chunk_reply_mismatch")

    for i in range(1, len(ch)):
        if ch[i].start_frame < ch[i - 1].start_frame:
            raise ValueError("chunks_out_of_order")
    if any(not isinstance(reply, TranscriptReply) for reply in rp):
        raise ValueError("invalid_reply")
    if any(
        not isinstance(chunk, AudioChunk)
        or type(chunk.sample_rate) is not int
        or chunk.sample_rate <= 0
        or type(chunk.start_frame) is not int
        or type(chunk.end_frame) is not int
        or not 0 <= chunk.start_frame < chunk.end_frame
        or not math.isclose(
            chunk.offset_seconds, chunk.start_frame / chunk.sample_rate, abs_tol=1e-9
        )
        for chunk in ch
    ):
        raise ValueError("invalid_chunk")
    if len({chunk.sample_rate for chunk in ch}) != 1:
        raise ValueError("sample_rate_mismatch")
    models = {r.model for r in rp}
    if len(models) != 1:
        raise ValueError("model_mismatch")

    merged_segments: list[Segment] = []
    text_parts: list[str] = []
    seen_exact: set[tuple[str, float | None, float | None]] = set()
    overlap_requires_review = False
    kept_intervals: list[tuple[float, float]] = []
    ordering: list[float] = []

    for idx, (chunk, reply) in enumerate(zip(ch, rp, strict=True)):
        if not isinstance(reply, TranscriptReply):
            raise ValueError("invalid_reply")
        if len(reply.text) > max_text_chars:
            raise ValueError("text_too_large")
        if idx > 0 and chunk.start_frame < ch[idx - 1].end_frame and reply.text.strip():
            overlap_requires_review = True
        dur = (chunk.end_frame - chunk.start_frame) / chunk.sample_rate
        validate_transcript(reply, dur, max_text_chars)

        for seg in reply.segments:
            rs, re = seg.start, seg.end
            if rs is not None and (not math.isfinite(rs) or rs < 0 or rs > dur):
                raise ValueError("segment_time_out_of_chunk")
            if re is not None and (not math.isfinite(re) or re < 0 or re > dur):
                raise ValueError("segment_time_out_of_chunk")
            if rs is not None and re is not None and rs > re:
                raise ValueError("invalid_segment_time")

            gs = (chunk.offset_seconds + rs) if rs is not None else None
            ge = (chunk.offset_seconds + re) if re is not None else None
            key = (seg.text, gs, ge)
            if gs is not None and ge is not None:
                if key in seen_exact:
                    continue
                seen_exact.add(key)

            if gs is not None and ge is not None:
                for ks, ke in kept_intervals:
                    if not (ge <= ks or gs >= ke):
                        overlap_requires_review = True
                        break
                kept_intervals.append((gs, ge))

            ordering.append(gs if gs is not None else chunk.offset_seconds)
            merged_segments.append(
                Segment(
                    segment_id=f"{idx}:{chunk.asset.id}:{seg.segment_id}",
                    text=seg.text,
                    start=gs,
                    end=ge,
                    speaker=seg.speaker,
                )
            )

        if reply.text.strip():
            text_parts.append(reply.text)

    merged_text = "\n".join(text_parts)
    if len(merged_text) > max_text_chars:
        raise ValueError("text_too_large")
    ordered = [
        segment
        for _, segment in sorted(
            zip(ordering, merged_segments, strict=True), key=lambda pair: pair[0]
        )
    ]
    return MergedTranscript(
        reply=TranscriptReply(model=rp[0].model, text=merged_text, segments=ordered),
        overlap_requires_review=overlap_requires_review,
    )
