# Transform math (view, projection, facing, rays)

Use native `math3d` as the numeric referee. Conventions in this harness: quaternion `[x, y, z, w]`, matrices **row-major**, right-handed, Y-up, camera looks down −Z (OpenGL-style). Mixing Unity column-major samples with OpenGL formulas is the usual silent bug.

## WebGL 1 greybox (this harness)

`uniformMatrix4fv(loc, false, columnMajor)`. `transpose=true` is `INVALID_VALUE` on WebGL 1 — the canvas stays a clear-color and the HUD still paints.

Put the camera at `(0, 5, 12)` looking at the origin. Draw at least three boxes with distinct colours (red / lime / gold) around the origin **on the first frame**, before any SLERP. `gl.clearColor` navy is fine; the *center* of a Chromium screenshot must show the meshes, not only HUD text.

Headless Chromium here uses software WebGL (ANGLE / SwiftShader). Do not require a GPU extension. Prefer raw WebGL in `index.html` — a Three.js CDN is often blocked.

## Vectors

Dot product `a·b = |a||b|cosθ`. For a unit view direction `v` and a unit surface normal `n`, `n·v > 0` means the face points toward the viewer (or along the ray). Backface culling is that test.

Cross product `a×b` is perpendicular to both. For a triangle `(p0,p1,p2)`, `normalize((p1-p0)×(p2-p0))` is the geometric normal. Winding order flips the sign — stay consistent with the renderer.

## Quaternions and SLERP

A unit quaternion rotates vectors: `q * v * q^{-1}`. Compose rotations with quaternion multiply (non-commutative).

SLERP interpolates at constant angular speed. Algorithm: take the short arc (if `q0·q1 < 0`, negate `q1`); if the dot is ~1, nlerp and normalize; else `sin` weights from `acos(dot)`. Linear lerp of xyzw without the short-arc fix will spin the long way or lose speed.

Euler angles are fine for editor UI, not for blending camera/ship orientation.

## View and projection

`look_at(eye, center, up)` builds a view matrix that places `eye` at the origin in view space and `center` on −Z. `perspective(fovy, aspect, near, far)` (fovy in **radians**) maps a frustum to clip space. Transform a point as `Pclip = Pproj * Pview * Pworld` with matching major-order.

Never copy a 4×4 from a blog without checking row vs column and the clip Z range (OpenGL −1..1 vs DirectX 0..1).

## Rays

Möller–Trumbore intersects a ray `(origin, direction)` with a triangle. It returns `t, u, v` barycentric; `t > 0` is in front of the origin. AABB slabs are the cheap reject before triangle tests.

Before writing C++/C#/GDScript, send the same numbers to `math3d` with `claimed` set to your result.
