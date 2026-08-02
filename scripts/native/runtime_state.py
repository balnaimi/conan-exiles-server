#!/usr/bin/env python3
"""Secure Native runtime lock and PID-state operations.

All persistent runtime state is opened relative to trusted directory descriptors
with O_NOFOLLOW so symlinks cannot redirect lock or PID writes.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import secrets
import stat
import sys
from pathlib import Path


def open_runtime_directory(game_dir: Path) -> tuple[int, int]:
    game = Path(os.path.abspath(game_dir))
    if game.is_symlink():
        raise RuntimeError(f"Game directory is a symlink: {game}")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        game_fd = os.open(game, flags)
    except OSError as exc:
        raise RuntimeError(f"Game directory is a symlink or not a directory: {game}") from exc
    try:
        try:
            os.mkdir(".runtime", mode=0o700, dir_fd=game_fd)
        except FileExistsError:
            pass
        try:
            runtime_fd = os.open(".runtime", flags, dir_fd=game_fd)
        except OSError as exc:
            raise RuntimeError("Persistent .runtime path is a symlink or not a directory") from exc
        os.fchmod(runtime_fd, 0o700)
        return game_fd, runtime_fd
    except Exception:
        os.close(game_fd)
        raise


def open_regular(runtime_fd: int, name: str, flags: int) -> int:
    try:
        descriptor = os.open(
            name,
            flags | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=runtime_fd,
        )
    except OSError as exc:
        raise RuntimeError(f"Runtime state {name} is a symlink or unsafe") from exc
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
            raise RuntimeError(f"Runtime state {name} is not a single regular file")
        os.fchmod(descriptor, 0o600)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def lock_exec(game_dir: Path, command: list[str]) -> None:
    if not command:
        raise RuntimeError("lock-exec requires a command")
    game_fd, runtime_fd = open_runtime_directory(game_dir)
    lock_fd: int | None = None
    fd9_installed = False
    try:
        lock_fd = open_regular(runtime_fd, "operation.lock", os.O_RDWR | os.O_CREAT)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another target operation is already running") from exc
        if lock_fd != 9:
            os.dup2(lock_fd, 9, inheritable=True)
            fd9_installed = True
            os.close(lock_fd)
            lock_fd = None
        else:
            os.set_inheritable(9, True)
            fd9_installed = True
            lock_fd = None
        os.close(runtime_fd)
        runtime_fd = -1
        os.close(game_fd)
        game_fd = -1
        environment = os.environ.copy()
        environment["NATIVE_OPERATION_LOCK_HELD"] = "1"
        try:
            os.execvpe(command[0], command, environment)
        except Exception:
            if fd9_installed:
                os.close(9)
                fd9_installed = False
            raise
    finally:
        if lock_fd is not None:
            try:
                os.close(lock_fd)
            except OSError:
                pass
        for descriptor in (runtime_fd, game_fd):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


def verify_lock(game_dir: Path) -> None:
    try:
        inherited = os.fstat(9)
    except OSError as exc:
        raise RuntimeError("Inherited FD 9 is missing") from exc
    if not stat.S_ISREG(inherited.st_mode) or inherited.st_nlink != 1:
        raise RuntimeError("Inherited FD 9 is not a single regular lock file")
    game_fd, runtime_fd = open_runtime_directory(game_dir)
    expected_fd: int | None = None
    try:
        expected_fd = open_regular(runtime_fd, "operation.lock", os.O_RDWR | os.O_CREAT)
        expected = os.fstat(expected_fd)
        if (inherited.st_dev, inherited.st_ino) != (expected.st_dev, expected.st_ino):
            raise RuntimeError("Inherited FD 9 does not reference operation.lock")
        try:
            fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Inherited FD 9 does not own the operation lock") from exc
        os.set_inheritable(9, True)
    finally:
        if expected_fd is not None:
            os.close(expected_fd)
        os.close(runtime_fd)
        os.close(game_fd)


def publish_pid(game_dir: Path, pid: int) -> None:
    if pid < 1:
        raise RuntimeError("PID must be positive")
    game_fd, runtime_fd = open_runtime_directory(game_dir)
    temporary = f".server.pid.{secrets.token_hex(8)}"
    descriptor: int | None = None
    try:
        descriptor = open_regular(
            runtime_fd,
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        )
        payload = f"{pid}\n".encode("ascii")
        written = 0
        while written < len(payload):
            written += os.write(descriptor, payload[written:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, "server.pid", src_dir_fd=runtime_fd, dst_dir_fd=runtime_fd)
        os.fsync(runtime_fd)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=runtime_fd)
        except FileNotFoundError:
            pass
        os.close(runtime_fd)
        os.close(game_fd)


def remove_pid(game_dir: Path, expected_pid: int) -> None:
    game_fd, runtime_fd = open_runtime_directory(game_dir)
    descriptor: int | None = None
    try:
        try:
            descriptor = os.open(
                "server.pid",
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=runtime_fd,
            )
        except FileNotFoundError:
            return
        except OSError as exc:
            raise RuntimeError("Runtime state server.pid is a symlink or unsafe") from exc
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
            raise RuntimeError("Runtime state server.pid is not a single regular file")
        payload = os.read(descriptor, 64).decode("ascii", errors="strict").strip()
        if payload != str(expected_pid):
            raise RuntimeError("Tracked server PID does not match the expected process")
        os.close(descriptor)
        descriptor = None
        os.unlink("server.pid", dir_fd=runtime_fd)
        os.fsync(runtime_fd)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(runtime_fd)
        os.close(game_fd)


def main() -> int:
    parser = argparse.ArgumentParser(description="Secure Native runtime state operations")
    subparsers = parser.add_subparsers(dest="action", required=True)

    lock_parser = subparsers.add_parser("lock-exec")
    lock_parser.add_argument("--game-dir", type=Path, required=True)
    lock_parser.add_argument("command", nargs=argparse.REMAINDER)

    verify_parser = subparsers.add_parser("verify-lock")
    verify_parser.add_argument("--game-dir", required=True, type=Path)

    publish_parser = subparsers.add_parser("publish-pid")
    publish_parser.add_argument("--game-dir", type=Path, required=True)
    publish_parser.add_argument("--pid", type=int, required=True)

    remove_parser = subparsers.add_parser("remove-pid")
    remove_parser.add_argument("--game-dir", type=Path, required=True)
    remove_parser.add_argument("--pid", type=int, required=True)

    args = parser.parse_args()
    try:
        if args.action == "lock-exec":
            command = args.command[1:] if args.command[:1] == ["--"] else args.command
            lock_exec(args.game_dir, command)
        elif args.action == "verify-lock":
            verify_lock(args.game_dir)
        elif args.action == "publish-pid":
            publish_pid(args.game_dir, args.pid)
        else:
            remove_pid(args.game_dir, args.pid)
    except (OSError, RuntimeError, UnicodeError) as exc:
        print(f"Runtime state error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
