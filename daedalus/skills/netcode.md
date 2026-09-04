---
name: netcode
description: Client-side prediction, server reconciliation, lag compensation — host is authority.
---

# SKILL: Netcode

**When**: multiplayer, lockstep, dedicated host, "it rubber-bands", hit registration, co-op.

**Do not** start netcode before the single-player slice is fun.

**Three pieces** (always together):
1. **Client-side prediction** — apply local input immediately to *your* pawn. Never wait a RTT to move.
2. **Server reconciliation** — host simulates truth. Client keeps a buffer of inputs; on snapshot, replay from the confirmed tick.
3. **Lag compensation** — host rewinds *other* pawns to the shooter's client time (clamped) for hitscan. Then restore. Not "the client decides the kill".

**How**:
1. Tick is the unit. Same `dt` on host and client. Serialize packed state (see skill `data_oriented_design`).
2. Send *inputs* (buttons, aim), not the full transform, for the local pawn.
3. Interpolate *remote* pawns (delay one snapshot). Predict only the local pawn.
4. Study `game_canon` id `mindustry` (`NetClient.java` / `NetServer.java`) — steal the split, do not copy GPL sources into an MIT game.

**Anti-patterns**:
- Client-authoritative position.
- Smoothing by lerping the local pawn toward the server (feels like ice).
- Lag comp without clamp → wallhacks.
- Claiming "perfect netcode" from a diagram with no tick buffer.
