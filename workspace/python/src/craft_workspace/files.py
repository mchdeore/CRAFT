"""Bounded file operations inside the managed workspace, using no-follow directory handles."""

import base64
import hashlib
import json
import os
import stat
import sys
import uuid

MAX_FILE = 25 * 1024 * 1024
MAX_WORKSPACE = 100 * 1024 * 1024


class FileError(Exception):
    pass


def parts(name):
    if (
        not isinstance(name, str)
        or not name
        or len(name) > 240
        or "\\" in name
        or any(ord(c) < 32 for c in name)
    ):
        raise FileError("invalid_name")
    values = name.split("/")
    if name.startswith("/") or any(
        value in {"", ".", ".."} or value.startswith(".") for value in values
    ):
        raise FileError("invalid_name")
    return values


def parent(name, create=False):
    values = parts(name)
    current = os.open("/workspace", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for value in values[:-1]:
            if create:
                try:
                    os.mkdir(value, 0o700, dir_fd=current)
                except FileExistsError:
                    pass
            following = os.open(value, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current)
            current = following
        return current, values[-1]
    except BaseException:
        os.close(current)
        raise


def listing():
    output, unsafe = [], []

    def walk(fd, prefix=""):
        for name in sorted(os.listdir(fd)):
            path = prefix + name
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    walk(child, path + "/")
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode) and not name.startswith("."):
                output.append({"name": path, "size": info.st_size})
            else:
                unsafe.append(path)

    root = os.open("/workspace", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        walk(root)
    finally:
        os.close(root)
    if unsafe:
        raise FileError("unsafe_files")
    if sum(row["size"] for row in output) > MAX_WORKSPACE:
        raise FileError("workspace_too_large")
    return output


def read(name):
    directory, leaf = parent(name)
    try:
        descriptor = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise FileError("unsafe_file")
            if info.st_size > MAX_FILE:
                raise FileError("file_too_large")
            chunks, count = [], 0
            while chunk := os.read(descriptor, min(65536, MAX_FILE + 1 - count)):
                count += len(chunk)
                if count > MAX_FILE:
                    raise FileError("file_too_large")
                chunks.append(chunk)
            value = b"".join(chunks)
            if len(value) != info.st_size:
                raise FileError("file_changed")
            return {
                "name": name,
                "size": len(value),
                "sha256": hashlib.sha256(value).hexdigest(),
                "content": base64.b64encode(value).decode(),
            }
        finally:
            os.close(descriptor)
    finally:
        os.close(directory)


def put(name, content):
    if len(content) > 10 * 1024 * 1024 or not name.endswith(".csv"):
        raise FileError("invalid_input")
    parts(name)
    if sum(row["size"] for row in listing()) + len(content) > MAX_WORKSPACE:
        raise FileError("workspace_too_large")
    directory, leaf = parent(name, create=True)
    temporary = ".input-" + uuid.uuid4().hex
    try:
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory
        )
        with os.fdopen(descriptor, "wb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.link(temporary, leaf, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass
        os.close(directory)
    return {"name": name, "size": len(content)}


def dispatch(operation, name=""):
    try:
        if operation == "list":
            result = listing()
        elif operation == "read":
            result = read(name)
        elif operation == "put":
            content = sys.stdin.buffer.read(10 * 1024 * 1024 + 1)
            result = put(name, content)
        else:
            raise FileError("invalid_operation")
        print(json.dumps({"ok": True, "result": result}))
    except Exception as error:
        code = str(error) if isinstance(error, FileError) else "file_operation_failed"
        print(json.dumps({"ok": False, "error": code}))
