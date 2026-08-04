#!/usr/bin/env python3
"""Dependency-free Source A2S_INFO readiness probe."""

from __future__ import annotations

import argparse
import json
import socket
import struct
import sys
from typing import Any

QUERY = b"\xff\xff\xff\xffTSource Engine Query\x00"


class A2SError(RuntimeError):
    pass


def read_cstring(payload: bytes, offset: int) -> tuple[str, int]:
    end = payload.find(b"\x00", offset)
    if end < 0:
        raise A2SError("unterminated string in A2S response")
    return payload[offset:end].decode("utf-8", errors="replace"), end + 1


def _request(host: str, port: int, timeout: float) -> tuple[bytes, tuple[str, int]]:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(QUERY, (host, port))
        payload, source = sock.recvfrom(65535)
        if payload[:5] == b"\xff\xff\xff\xffA":
            if len(payload) < 9:
                raise A2SError("truncated A2S challenge")
            sock.sendto(QUERY + payload[5:9], (host, port))
            payload, source = sock.recvfrom(65535)

    if payload[:5] != b"\xff\xff\xff\xffI":
        raise A2SError(f"unexpected A2S response header {payload[:5].hex()}")
    return payload, source


def query_counts(host: str, port: int, timeout: float) -> dict[str, int]:
    """Return only player counts without decoding server identity strings."""
    payload, _source = _request(host, port, timeout)
    offset = 6
    for _ in range(4):
        end = payload.find(b"\x00", offset)
        if end < 0:
            raise A2SError("unterminated string in A2S response")
        offset = end + 1
    if len(payload) < offset + 5:
        raise A2SError("truncated A2S numeric fields")
    players, max_players, bots = payload[offset + 2 : offset + 5]
    return {"players": players, "max_players": max_players, "bots": bots}


def query(host: str, port: int, timeout: float) -> dict[str, Any]:
    payload, source = _request(host, port, timeout)

    offset = 5
    if len(payload) <= offset:
        raise A2SError("truncated A2S protocol field")
    protocol = payload[offset]
    offset += 1
    name, offset = read_cstring(payload, offset)
    map_name, offset = read_cstring(payload, offset)
    folder, offset = read_cstring(payload, offset)
    game, offset = read_cstring(payload, offset)
    if len(payload) < offset + 9:
        raise A2SError("truncated A2S numeric fields")
    app_id = struct.unpack_from("<H", payload, offset)[0]
    offset += 2
    players, max_players, bots = payload[offset : offset + 3]
    offset += 3
    server_type = chr(payload[offset])
    environment = chr(payload[offset + 1])
    visibility = payload[offset + 2]
    vac = payload[offset + 3]
    offset += 4
    version, offset = read_cstring(payload, offset)
    return {
        "source": f"{source[0]}:{source[1]}",
        "protocol": protocol,
        "name": name,
        "map": map_name,
        "folder": folder,
        "game": game,
        "app_id": app_id,
        "players": players,
        "max_players": max_players,
        "bots": bots,
        "server_type": server_type,
        "environment": environment,
        "passworded": visibility,
        "vac": vac,
        "version": version,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe a Source-compatible A2S_INFO endpoint")
    parser.add_argument("host")
    parser.add_argument("port", type=int)
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        info = query(args.host, args.port, args.timeout)
    except (A2SError, OSError, ValueError) as exc:
        print(f"A2S probe failed: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(info, sort_keys=True))
    else:
        print(f"A2S ready: {info['name']} ({info['map']}) {info['players']}/{info['max_players']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
