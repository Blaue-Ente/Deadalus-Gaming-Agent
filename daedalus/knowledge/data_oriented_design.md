# Data-oriented design for games

Games fail the frame budget when the CPU spends the tick chasing pointers instead of doing math. Data-oriented design (DOD) is the habit of laying out bytes so the inner loop streams through cache lines.

A cache line is typically 64 bytes. If each enemy is a heap object with a vtable, a transform pointer, a physics pointer, and a render pointer, one "update all enemies" pass can miss the cache once per field. If the same fields live in an array of structs (or a struct of arrays for SIMD), the prefetcher does the work.

## What the hot path may touch

Per tick, for N units: position, velocity, alive flag, team, aim. That is the simulation record. Animations, UI names, loot tables belong on a cold side table keyed by the same id.

Array of structures (AoS) is the default: `struct Unit { vec3 pos; vec3 vel; u16 hp; u8 team; } units[MAX]`. Structure of arrays (SoA) wins when one field is streamed alone (all positions for a broadphase).

## Ownership

Whoever allocates the buffer owns it. `new Enemy()` per spawn without an arena or pool fragments the heap and hides lifetime. Object pools and packed arrays (swap-with-last on death) keep the live range contiguous — the same idea as idList in large C++ engines and stretchy buffers in stb_ds.

## What DOD is not

Naming a folder `ecs/` does not make the data packed. A `List<MonoBehaviour>` that `GetComponent`s in `Update` is still a pointer graph. Virtual `virtual void Tick()` on a 10k-unit path is a mispredict plus a miss.

Measure: `profiler` on a Chrome trace or engine log. If p95 > 16.7 ms, look at what the loop loads, not at class diagrams.

## Net snapshots

The layout you simulate should be the layout you serialize. A packed tick buffer is what client-side prediction replays. See also knowledge `netcode.md`.
