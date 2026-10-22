"""Reentrant process/thread locking for simulated-provider metadata."""

import fcntl
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from threading import RLock
from typing import Any


@dataclass
class _State:
    mutex: Any = field(default_factory=RLock)
    depth: int = 0
    descriptor: int | None = None


_states: dict[Path, _State] = {}
_guard = RLock()


def _after_fork() -> None:
    global _states, _guard
    # Closing inherited descriptors must not explicitly unlock the parent's lock.
    for state in _states.values():
        if state.descriptor is not None:
            os.close(state.descriptor)
    _states = {}
    _guard = RLock()


os.register_at_fork(after_in_child=_after_fork)


@contextmanager
def provider_lock(meta: Path, *, create: bool = True) -> Iterator[None]:
    with _guard:
        state = _states.setdefault(meta, _State())
    with state.mutex:
        owner = os.getpid()
        if state.depth == 0:
            if create:
                meta.mkdir(parents=True, exist_ok=True)
            try:
                state.descriptor = os.open(
                    meta / "lock", os.O_RDWR | os.O_CREAT if create else os.O_RDONLY, 0o600
                )
            except FileNotFoundError:
                if create:
                    raise
                # Read-only mounts with no metadata have nothing to coordinate yet.
                yield
                return
            try:
                fcntl.flock(state.descriptor, fcntl.LOCK_EX)
            except BaseException:
                os.close(state.descriptor)
                state.descriptor = None
                raise
        state.depth += 1
        try:
            yield
        finally:
            if os.getpid() == owner:
                state.depth -= 1
                if state.depth == 0 and state.descriptor is not None:
                    try:
                        fcntl.flock(state.descriptor, fcntl.LOCK_UN)
                    finally:
                        os.close(state.descriptor)
                        state.descriptor = None


def _decorate[T](method: Callable[..., T], *, create: bool) -> Callable[..., T]:
    @wraps(method)
    def run(provider: Any, *args: Any, **kwargs: Any) -> T:
        with provider_lock(provider.meta, create=create):
            return method(provider, *args, **kwargs)

    return run


def synchronized[T](method: Callable[..., T]) -> Callable[..., T]:
    return _decorate(method, create=True)


def synchronized_read[T](method: Callable[..., T]) -> Callable[..., T]:
    return _decorate(method, create=False)
