#!/usr/bin/env python3
"""Minimal Source RCON client. Password is read from environment/file, never argv."""

from __future__ import annotations

import argparse
import os
import socket
import struct
import sys
from pathlib import Path

AUTH = 3
AUTH_RESPONSE = 2
EXEC_COMMAND = 2
RESPONSE_VALUE = 0


class RconError(RuntimeError):
    pass


def password_from_environment() -> str:
    direct = os.environ.get("RCON_PASSWORD", "")
    file_path = os.environ.get("RCON_PASSWORD_FILE", "")
    if direct and file_path:
        raise RconError("RCON_PASSWORD and RCON_PASSWORD_FILE are both set")
    if file_path:
        lines = Path(file_path).read_text(encoding="utf-8").splitlines()
        if len(lines) != 1 or not lines[0]:
            raise RconError("RCON_PASSWORD_FILE must contain exactly one logical line")
        direct = lines[0]
    if not direct:
        raise RconError("RCON password is not configured")
    return direct


def packet(request_id: int, packet_type: int, body: str) -> bytes:
    payload = struct.pack("<ii", request_id, packet_type) + body.encode("utf-8") + b"\x00\x00"
    return struct.pack("<i", len(payload)) + payload


def receive_exact(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = sock.recv(size - len(chunks))
        if not chunk:
            raise RconError("RCON connection closed unexpectedly")
        chunks.extend(chunk)
    return bytes(chunks)


def receive_packet(sock: socket.socket) -> tuple[int, int, str]:
    size = struct.unpack("<i", receive_exact(sock, 4))[0]
    if size < 10 or size > 4 * 1024 * 1024:
        raise RconError(f"invalid RCON packet size {size}")
    payload = receive_exact(sock, size)
    if payload[-2:] != b"\x00\x00":
        raise RconError("invalid RCON packet terminator")
    request_id, packet_type = struct.unpack("<ii", payload[:8])
    return request_id, packet_type, payload[8:-2].decode("utf-8", errors="replace")


def execute(host: str, port: int, command: str, timeout: float) -> str:
    password = password_from_environment()
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        auth_id = 991
        sock.sendall(packet(auth_id, AUTH, password))
        for _ in range(2):
            response_id, response_type, _ = receive_packet(sock)
            if response_id == -1:
                raise RconError("RCON authentication failed")
            if response_id == auth_id and response_type == AUTH_RESPONSE:
                break
        else:
            raise RconError("RCON authentication response was not received")

        command_id = 992
        sock.sendall(packet(command_id, EXEC_COMMAND, command))
        response_id, response_type, body = receive_packet(sock)
        if response_id != command_id or response_type not in (RESPONSE_VALUE, AUTH_RESPONSE):
            raise RconError("unexpected RCON command response")
        return body


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a Conan Source RCON command")
    parser.add_argument("--host", default=os.environ.get("RCON_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("RCON_PORT", "25575")))
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("command", nargs="+", help="RCON command and arguments")
    args = parser.parse_args()
    try:
        response = execute(args.host, args.port, " ".join(args.command), args.timeout)
    except (OSError, ValueError, RconError) as exc:
        print(f"RCON command failed: {exc}", file=sys.stderr)
        return 1
    if response:
        print(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
