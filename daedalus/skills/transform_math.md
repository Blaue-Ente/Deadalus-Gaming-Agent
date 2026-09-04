---
name: transform_math
description: Verify 3D math with math3d before embedding SLERP, ray-triangle, look-at, or projection matrices in game code.
---

# SKILL: Transform math

**When**: quaternions, SLERP, look-at, view/projection matrices, raycasts, normals, visibility (n·v).

**How**:
1. Call native `math3d` with the named op and operands. Pass `claimed` if you already computed a result — mismatch means the formula is wrong.
2. Conventions: quat `[x,y,z,w]`, mat4 **row-major**, OpenGL Y-up, camera looks down −Z.
3. Dot = facing. Cross(edge1, edge2) = triangle normal. SLERP takes the short arc (negate if dot < 0).
4. Then write C++/C#/GDScript from the verified numbers. CLI: `python -m daedalus.math3d '{"op":"quat_slerp","q0":[0,0,0,1],"q1":[0,0,0.7071,0.7071],"t":0.5}'`

**Ops**: `dot` `cross` `facing` `triangle_normal` `quat_slerp` `quat_rotate` `look_at` `perspective` `ray_triangle` `ray_aabb` `mat4_mul`

**Anti-patterns**:
- Guessing SLERP lerp vs spherical. Euler-angle camera for a full 3D ship.
- Pasting a StackOverflow matrix with mixed row/column major.
- Skipping `math3d` because "the engine will fix it".
