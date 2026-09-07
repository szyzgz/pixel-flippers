"""Client for Azahar's RPC server — read (and write) the emulated 3DS memory
over UDP, so the 3DS tier can KNOW the player's position instead of navigating
blind by screenshots.

Protocol (from Citra's scripting/citra.py): UDP to 127.0.0.1:45987, little-
endian header struct "<IIII" = (version=1, request_id, request_type, data_size),
then the payload. ReadMemory=1 payload = "<II"(address, size) with size<=32 per
request (larger reads are chunked). The reply is a 16-byte header + the bytes.

Enable it first: set enable_rpc_server=true in Azahar's qt-config.ini (while
Azahar is closed — it rewrites config on exit), then relaunch.
"""

from __future__ import annotations

import random
import socket
import struct

RPC_PORT = 45987
CURRENT_REQUEST_VERSION = 1
MAX_REQUEST_DATA_SIZE = 32
READ_MEMORY = 1
WRITE_MEMORY = 2


class RPCError(Exception):
    pass


class AzaharRPC:
    def __init__(self, host: str = "127.0.0.1", port: int = RPC_PORT, timeout: float = 2.0):
        self._addr = (host, port)
        self._timeout = timeout
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.settimeout(timeout)

    def _request(self, req_type: int, payload: bytes) -> bytes:
        req_id = random.randint(0, 0xFFFFFFFF)
        header = struct.pack("<IIII", CURRENT_REQUEST_VERSION, req_id, req_type, len(payload))
        self._sock.sendto(header + payload, self._addr)
        # retry a couple of times for stray/mismatched datagrams
        for _ in range(4):
            try:
                raw = self._sock.recv(16 + MAX_REQUEST_DATA_SIZE + 16)
            except socket.timeout as e:
                raise RPCError(f"RPC timeout — is Azahar running with 'Enable RPC server' on? ({self._addr[0]}:{self._addr[1]})") from e
            if len(raw) < 16:
                continue
            version, rid, rtype, dsize = struct.unpack("<IIII", raw[:16])
            if rid == req_id and rtype == req_type:
                return raw[16:16 + dsize]
        raise RPCError("no matching RPC reply")

    def read_memory(self, address: int, size: int) -> bytes:
        out = b""
        remaining = size
        addr = address
        while remaining > 0:
            chunk = min(remaining, MAX_REQUEST_DATA_SIZE)
            data = self._request(READ_MEMORY, struct.pack("<II", addr, chunk))
            if len(data) < chunk:
                raise RPCError(f"short read at 0x{addr:08X}: got {len(data)}/{chunk}")
            out += data[:chunk]
            addr += chunk
            remaining -= chunk
        return out

    def read_u8(self, address: int) -> int:
        return self.read_memory(address, 1)[0]

    def read_u16(self, address: int) -> int:
        return struct.unpack("<H", self.read_memory(address, 2))[0]

    def read_u32(self, address: int) -> int:
        return struct.unpack("<I", self.read_memory(address, 4))[0]

    def read_f32(self, address: int) -> float:
        return struct.unpack("<f", self.read_memory(address, 4))[0]

    def close(self) -> None:
        self._sock.close()
