# Multiplayer models: prediction, reconciliation, lag compensation

Single-player fun first. Netcode is a tick protocol, not a folder of RPCs.

## Authority

The host (dedicated or listen) is truth. The client may *predict* its own pawn; it may not declare kills, inventory, or other pawns' positions.

## Client-side prediction

Input is sampled, given a tick id, applied immediately to the local pawn, and sent to the host. Without this, movement waits a round-trip and feels like ice. Predicted state is a hypothesis.

## Server reconciliation

The host simulates the same input stream. It returns a snapshot: tick id + packed pose (and whatever else is authoritative). The client:

1. Finds the snapshot tick in its input buffer.
2. Snaps the local pawn to the host pose.
3. Re-applies every later local input.

If prediction matches, the snap is a no-op. If it mismatches (you got pushed), you correct once, then predict from there. Do not lerp the local pawn toward the server every frame — that is the rubber-band.

Remote pawns are *interpolated* (render one snapshot behind), not predicted, unless you have a special case (driving a vehicle together).

## Lag compensation

Hitscan and melee need the world as the shooter *saw* it. The host keeps a short history of remote poses, rewinds to `client_time` (clamped to a max rewind, e.g. 200–250 ms), runs the trace, then restores. Unclamped rewind is a wallhack. The shooter still does not decide the hit: the host does, on rewound data.

Projectiles can be simulated on the host with a spawn tick instead of rewind.

## Layout

One tick = one contiguous blob you can memcpy. That is why DOD and netcode share a brain. Mindustry's NetClient/NetServer split is a study specimen (`game_canon` id `mindustry`) — GPL code stays in the canon jail, not in an MIT Playground game.

## Failure modes

- Client-authoritative transform.
- Snapshot without a tick id (cannot replay).
- Different `dt` on host vs client.
- Predicting remote pawns "to be responsive" (everyone desyncs).
