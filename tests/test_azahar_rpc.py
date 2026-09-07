"""Tests for the Azahar RPC memory client, against a fake UDP server that
speaks the same protocol."""

import socket
import struct
import threading

import pytest

from pixel_flippers.azahar_rpc import AzaharRPC, RPCError


def _fake_server(memory: bytes):
    """Start a UDP server that answers ReadMemory from `memory`. Returns port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]

    def serve():
        while True:
            try:
                data, addr = sock.recvfrom(64)
            except OSError:
                return
            version, rid, rtype, dsize = struct.unpack("<IIII", data[:16])
            if rtype == 1:  # ReadMemory
                address, size = struct.unpack("<II", data[16:24])
                chunk = memory[address:address + size]
                sock.sendto(struct.pack("<IIII", version, rid, rtype, len(chunk)) + chunk, addr)

    threading.Thread(target=serve, daemon=True).start()
    return port, sock


def test_read_memory_and_typed_reads():
    mem = bytes(range(256))
    port, sock = _fake_server(mem)
    try:
        r = AzaharRPC(port=port, timeout=1.0)
        assert r.read_memory(0x10, 4) == bytes([0x10, 0x11, 0x12, 0x13])
        assert r.read_u8(0x05) == 5
        assert r.read_u16(0x00) == 0x0100
        assert r.read_u32(0x00) == 0x03020100
    finally:
        sock.close()


def test_read_memory_chunks_large_reads():
    # 100 bytes > 32-byte cap → must chunk and stitch correctly
    mem = bytes((i * 7) % 256 for i in range(300))
    port, sock = _fake_server(mem)
    try:
        r = AzaharRPC(port=port, timeout=1.0)
        assert r.read_memory(0, 100) == mem[:100]
    finally:
        sock.close()


def test_timeout_raises_clear_error():
    # nothing listening → timeout with a helpful message
    r = AzaharRPC(port=59999, timeout=0.3)
    with pytest.raises(RPCError, match="timeout"):
        r.read_u32(0x08000000)
