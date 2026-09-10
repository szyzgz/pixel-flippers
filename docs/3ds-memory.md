# 3DS memory reads (Azahar RPC) — player position

Azahar (the Citra successor) ships an optional RPC server: a small UDP service
on `127.0.0.1:45987` that answers ReadMemory/WriteMemory requests against the
emulated 3DS address space. `pixel_flippers.azahar_rpc.AzaharRPC` speaks it.

Enable it once, with Azahar **closed** (it rewrites its config on exit): in
`qt-config.ini` set both `enable_rpc_server=true` and
`enable_rpc_server\default=false`, then relaunch.

With it on, the `n3ds` backend gains the `position` capability and a
`read_position` tool that returns the player's live world coordinates — cheap
(no image), so an agent can confirm it actually moved instead of paying for a
screenshot.

## The Pokémon Ultra Sun position struct

Found empirically (not from any public map) by a differential scan: snapshot
RAM, take a step, snapshot again, keep the 4-byte values that changed by a
consistent amount and reversed when walking back. Result:

| address      | type    | meaning              |
|--------------|---------|----------------------|
| `0x300068C4` | float32 | X (east +, west −)   |
| `0x300068C8` | float32 | Y (height/elevation) |
| `0x300068CC` | float32 | Z (north −, south +) |

`POS_ADDR` in `n3ds_backend.py` points at X; env `PIXEL_FLIPPERS_POS_ADDR`
overrides it. The struct is mirrored (entity + camera + collision copies), so
several addresses hold the same values — any stable one works.

### Caveats
- **The map grid is rotated ~45° vs the screen.** Screen-south moves world
  `(+X, +Z)` in equal parts; screen-east moves `(+X, −Z)`. Treat `(X, Z)` as a
  2-D point and derive screen-relative directions from the delta vector (the
  harness does this): `east = (dX − dZ)/√2`, `south = (dX + dZ)/√2`.
- These are the **smoothed render** coordinates, so they drift sub-unit while
  "standing still" (idle model sway). Good to sub-tile, but not the integer
  tile index.
- The `0x30000000`-region address can shift across map loads or emulator
  sessions. A NaN / out-of-range read is the signal to re-locate it.

## Re-finding the address (differential scan)

If the address stops reading sane values, re-run the scan: with the character
in a walkable overworld, snapshot a region (the `0x30000000` heap is the
productive one, plus the first ~4 MiB of `0x08000000`), step in a known
direction a few times taking a snapshot after each, then step back. Keep the
4-byte offsets whose float value ramped by a consistent amount each forward
step and reversed on the way back — the true coordinates show up replicated
across many addresses, which noise never does. A batched UDP reader (many
requests in flight) makes a full pass a few seconds instead of minutes.
