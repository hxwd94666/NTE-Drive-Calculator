#!/usr/bin/env python3
"""Query NTE Script v4 custom events through the local IPC v7 pipe."""

from __future__ import annotations

import argparse
import json
import struct
import time


PIPE_NAME = r"\\.\pipe\nte-mods-plugin-v7"
IPC_MAGIC = 0x5145544E
IPC_VERSION = 7
QUERY_MOD_EVENTS = 12
REQUEST_SIZE = 1080
RESPONSE_SIZE = 2072
RESPONSE_HEADER = struct.Struct("<IHHQII")
MOD_EVENT = struct.Struct("<QQ32s32sII3Q")
STATUS_OK = 1


def _fixed_ascii(value: bytes) -> str:
    return value.split(b"\0", 1)[0].decode("ascii")


def query(request_id: int) -> list[dict[str, object]]:
    request = bytearray(REQUEST_SIZE)
    struct.pack_into(
        "<IHHQ", request, 0, IPC_MAGIC, IPC_VERSION, QUERY_MOD_EVENTS, request_id
    )
    with open(PIPE_NAME, "r+b", buffering=0) as pipe:
        pipe.write(request)
        response = pipe.read(RESPONSE_SIZE)
    if len(response) != RESPONSE_SIZE:
        raise RuntimeError(f"short IPC response: {len(response)} bytes")

    magic, version, reserved, response_id, status, count = RESPONSE_HEADER.unpack_from(
        response
    )
    if (magic, version, reserved, response_id) != (
        IPC_MAGIC,
        IPC_VERSION,
        0,
        request_id,
    ):
        raise RuntimeError("invalid IPC response header")
    if status != STATUS_OK:
        raise RuntimeError(f"plugin status {status}")
    if count > 18:
        raise RuntimeError(f"invalid event count {count}")

    events: list[dict[str, object]] = []
    for index in range(count):
        (
            sequence,
            timestamp_100ns,
            mod_id,
            name,
            value_count,
            event_reserved,
            *values,
        ) = MOD_EVENT.unpack_from(response, RESPONSE_HEADER.size + index * MOD_EVENT.size)
        if event_reserved != 0 or value_count > 3:
            raise RuntimeError("invalid mod event record")
        events.append(
            {
                "sequence": sequence,
                "timestamp_100ns": timestamp_100ns,
                "mod_id": _fixed_ascii(mod_id),
                "name": _fixed_ascii(name),
                "values": values[:value_count],
            }
        )
    return events


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--follow", action="store_true", help="poll for new events")
    parser.add_argument("--interval", type=float, default=0.25)
    args = parser.parse_args()

    request_id = 1
    last_sequence = 0
    while True:
        for event in query(request_id):
            sequence = int(event["sequence"])
            if sequence > last_sequence:
                print(json.dumps(event, ensure_ascii=False), flush=True)
                last_sequence = sequence
        if not args.follow:
            return
        request_id += 1
        time.sleep(max(args.interval, 0.05))


if __name__ == "__main__":
    main()
