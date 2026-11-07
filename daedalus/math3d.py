"""Reference 3D math for the agent — verify formulas before they land in game code.

Named ops (dot, SLERP, Möller–Trumbore, look-at, perspective) run in-process
with stdlib floats. Numpy is an optional cross-check, not a dependency.

Native tool: ``math3d``. CLI: ``python -m daedalus.math3d '<json>'``.
"""
from __future__ import annotations

import json
import math
import sys
from typing import Any

EPS = 1e-6
ATOL = 1e-5

OPS = (
    "add", "sub", "scale", "length", "normalize",
    "dot", "cross", "facing", "triangle_normal",
    "quat_mul", "quat_normalize", "quat_slerp", "quat_from_axis_angle", "quat_rotate",
    "mat4_mul", "mat4_transform", "look_at", "perspective",
    "ray_triangle", "ray_aabb",
    "catalog",
)


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _maybe_parse(v: Any) -> Any:
    """Models often pass vec/quat as the string '[0,0,0,1]' instead of a JSON array."""
    if isinstance(v, str):
        s = v.strip()
        if s[:1] in "[{":
            try:
                return json.loads(s)
            except json.JSONDecodeError:
                return v
    return v


def vec3(v: Any) -> tuple[float, float, float]:
    v = _maybe_parse(v)
    if v is None:
        return (0.0, 0.0, 0.0)
    if isinstance(v, dict):
        return (_num(v.get("x")), _num(v.get("y")), _num(v.get("z")))
    if isinstance(v, (list, tuple)) and len(v) >= 3:
        return (_num(v[0]), _num(v[1]), _num(v[2]))
    raise ValueError("expected vec3 as [x,y,z] or {x,y,z}")


def quat(v: Any) -> tuple[float, float, float, float]:
    """Quaternion as (x, y, z, w) — same as glm / Unity."""
    v = _maybe_parse(v)
    if v is None:
        return (0.0, 0.0, 0.0, 1.0)
    if isinstance(v, dict):
        return (_num(v.get("x")), _num(v.get("y")), _num(v.get("z")), _num(v.get("w"), 1.0))
    if isinstance(v, (list, tuple)) and len(v) >= 4:
        return (_num(v[0]), _num(v[1]), _num(v[2]), _num(v[3]))
    raise ValueError("expected quat as [x,y,z,w] or {x,y,z,w}")


def mat4(v: Any) -> tuple[float, ...]:
    v = _maybe_parse(v)
    if v is None:
        return identity()
    if isinstance(v, (list, tuple)) and len(v) == 16:
        return tuple(_num(x) for x in v)
    if isinstance(v, (list, tuple)) and len(v) == 4 and all(isinstance(r, (list, tuple)) for r in v):
        rows = []
        for row in v:
            rows.extend(_num(x) for x in list(row)[:4])
        if len(rows) == 16:
            return tuple(rows)
    raise ValueError("expected mat4 as 16 floats (row-major) or 4x4 rows")


def identity() -> tuple[float, ...]:
    return (
        1.0, 0.0, 0.0, 0.0,
        0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        0.0, 0.0, 0.0, 1.0,
    )


def add(a: Any, b: Any) -> tuple[float, float, float]:
    ax, ay, az = vec3(a)
    bx, by, bz = vec3(b)
    return (ax + bx, ay + by, az + bz)


def sub(a: Any, b: Any) -> tuple[float, float, float]:
    ax, ay, az = vec3(a)
    bx, by, bz = vec3(b)
    return (ax - bx, ay - by, az - bz)


def scale(a: Any, s: Any) -> tuple[float, float, float]:
    x, y, z = vec3(a)
    k = _num(s)
    return (x * k, y * k, z * k)


def length(a: Any) -> float:
    x, y, z = vec3(a)
    return math.sqrt(x * x + y * y + z * z)


def normalize(a: Any) -> tuple[float, float, float]:
    n = length(a)
    if n < EPS:
        return (0.0, 0.0, 0.0)
    return scale(a, 1.0 / n)


def dot(a: Any, b: Any) -> float:
    ax, ay, az = vec3(a)
    bx, by, bz = vec3(b)
    return ax * bx + ay * by + az * bz


def cross(a: Any, b: Any) -> tuple[float, float, float]:
    ax, ay, az = vec3(a)
    bx, by, bz = vec3(b)
    return (ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx)


def facing(view: Any, normal: Any, *, eps: float = 0.0) -> dict[str, Any]:
    """Front-face test: n·v > eps means the surface faces the viewer/ray."""
    d = dot(normalize(view), normalize(normal))
    return {"dot": d, "visible": d > eps}


def triangle_normal(v0: Any, v1: Any, v2: Any) -> tuple[float, float, float]:
    return normalize(cross(sub(v1, v0), sub(v2, v0)))


def quat_mul(a: Any, b: Any) -> tuple[float, float, float, float]:
    ax, ay, az, aw = quat(a)
    bx, by, bz, bw = quat(b)
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def quat_normalize(q: Any) -> tuple[float, float, float, float]:
    x, y, z, w = quat(q)
    n = math.sqrt(x * x + y * y + z * z + w * w)
    if n < EPS:
        return (0.0, 0.0, 0.0, 1.0)
    return (x / n, y / n, z / n, w / n)


def quat_from_axis_angle(axis: Any, angle: Any) -> tuple[float, float, float, float]:
    ax, ay, az = normalize(axis)
    half = _num(angle) * 0.5
    s = math.sin(half)
    return (ax * s, ay * s, az * s, math.cos(half))


def quat_rotate(q: Any, v: Any) -> tuple[float, float, float]:
    """Rotate vector v by unit quaternion q (q * v * q^{-1})."""
    qn = quat_normalize(q)
    x, y, z = vec3(v)
    qv = (x, y, z, 0.0)
    qi = (-qn[0], -qn[1], -qn[2], qn[3])
    r = quat_mul(quat_mul(qn, qv), qi)
    return (r[0], r[1], r[2])


def quat_slerp(q0: Any, q1: Any, t: Any) -> tuple[float, float, float, float]:
    a = quat_normalize(q0)
    b = quat_normalize(q1)
    tt = max(0.0, min(1.0, _num(t)))
    d = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3]
    if d < 0.0:
        b = (-b[0], -b[1], -b[2], -b[3])
        d = -d
    if d > 0.9995:
        return quat_normalize((
            a[0] + tt * (b[0] - a[0]),
            a[1] + tt * (b[1] - a[1]),
            a[2] + tt * (b[2] - a[2]),
            a[3] + tt * (b[3] - a[3]),
        ))
    d = max(-1.0, min(1.0, d))
    theta_0 = math.acos(d)
    theta = theta_0 * tt
    s0 = math.sin(theta_0 - theta) / math.sin(theta_0)
    s1 = math.sin(theta) / math.sin(theta_0)
    return (
        s0 * a[0] + s1 * b[0],
        s0 * a[1] + s1 * b[1],
        s0 * a[2] + s1 * b[2],
        s0 * a[3] + s1 * b[3],
    )


def mat4_mul(a: Any, b: Any) -> tuple[float, ...]:
    A = mat4(a)
    B = mat4(b)
    out = [0.0] * 16
    for r in range(4):
        for c in range(4):
            s = 0.0
            for k in range(4):
                s += A[r * 4 + k] * B[k * 4 + c]
            out[r * 4 + c] = s
    return tuple(out)


def mat4_transform(m: Any, p: Any, w: float = 1.0) -> tuple[float, float, float]:
    x, y, z = vec3(p)
    M = mat4(m)
    nx = M[0] * x + M[1] * y + M[2] * z + M[3] * w
    ny = M[4] * x + M[5] * y + M[6] * z + M[7] * w
    nz = M[8] * x + M[9] * y + M[10] * z + M[11] * w
    nw = M[12] * x + M[13] * y + M[14] * z + M[15] * w
    if abs(nw) > EPS and w != 0.0:
        nx, ny, nz = nx / nw, ny / nw, nz / nw
    return (nx, ny, nz)


def look_at(eye: Any, center: Any, up: Any = (0.0, 1.0, 0.0)) -> tuple[float, ...]:
    """OpenGL-style view matrix (right-handed, Y-up, camera looks down −Z). Row-major."""
    e = vec3(eye)
    f = normalize(sub(center, eye))
    s = normalize(cross(f, up))
    if length(s) < EPS:
        s = normalize(cross(f, (1.0, 0.0, 0.0)))
        if length(s) < EPS:
            s = (1.0, 0.0, 0.0)
    u = cross(s, f)
    return (
        s[0], s[1], s[2], -dot(s, e),
        u[0], u[1], u[2], -dot(u, e),
        -f[0], -f[1], -f[2], dot(f, e),
        0.0, 0.0, 0.0, 1.0,
    )


def perspective(fovy: Any, aspect: Any, znear: Any, zfar: Any) -> tuple[float, ...]:
    """OpenGL clip-space perspective. ``fovy`` in radians. Row-major."""
    fovy_r = _num(fovy)
    asp = _num(aspect, 1.0)
    n = _num(znear, 0.1)
    f = _num(zfar, 100.0)
    if abs(math.sin(fovy_r / 2.0)) < EPS or abs(asp) < EPS or abs(n - f) < EPS:
        raise ValueError("degenerate perspective (fovy/aspect/near/far)")
    e = 1.0 / math.tan(fovy_r / 2.0)
    a = (f + n) / (n - f)
    b = (2.0 * f * n) / (n - f)
    return (
        e / asp, 0.0, 0.0, 0.0,
        0.0, e, 0.0, 0.0,
        0.0, 0.0, a, b,
        0.0, 0.0, -1.0, 0.0,
    )


def ray_triangle(
    origin: Any,
    direction: Any,
    v0: Any,
    v1: Any,
    v2: Any,
    *,
    cull_backface: bool = False,
) -> dict[str, Any]:
    """Möller–Trumbore. ``direction`` need not be unit length; ``t`` is in that space."""
    orig = vec3(origin)
    dire = vec3(direction)
    a = vec3(v0)
    b = vec3(v1)
    c = vec3(v2)
    edge1 = sub(b, a)
    edge2 = sub(c, a)
    h = cross(dire, edge2)
    det = dot(edge1, h)
    if cull_backface and det < EPS:
        return {"hit": False, "reason": "backface_or_parallel"}
    if abs(det) < EPS:
        return {"hit": False, "reason": "parallel"}
    inv = 1.0 / det
    s = sub(orig, a)
    u = inv * dot(s, h)
    if u < 0.0 or u > 1.0:
        return {"hit": False, "reason": "u"}
    q = cross(s, edge1)
    v = inv * dot(dire, q)
    if v < 0.0 or u + v > 1.0:
        return {"hit": False, "reason": "v"}
    t = inv * dot(edge2, q)
    if t <= EPS:
        return {"hit": False, "reason": "behind", "t": t}
    point = add(orig, scale(dire, t))
    return {"hit": True, "t": t, "u": u, "v": v, "point": point}


def ray_aabb(origin: Any, direction: Any, bmin: Any, bmax: Any) -> dict[str, Any]:
    """Slab test. Miss when the ray is parallel and outside a slab."""
    o = vec3(origin)
    d = vec3(direction)
    lo = vec3(bmin)
    hi = vec3(bmax)
    tmin = 0.0
    tmax = math.inf
    for i in range(3):
        if abs(d[i]) < EPS:
            if o[i] < lo[i] or o[i] > hi[i]:
                return {"hit": False, "reason": "parallel_out"}
            continue
        inv = 1.0 / d[i]
        t1 = (lo[i] - o[i]) * inv
        t2 = (hi[i] - o[i]) * inv
        if t1 > t2:
            t1, t2 = t2, t1
        tmin = max(tmin, t1)
        tmax = min(tmax, t2)
        if tmax < tmin:
            return {"hit": False, "reason": "slab"}
    point = add(o, scale(d, tmin))
    return {"hit": True, "t": tmin, "t_exit": tmax, "point": point}


def _round_num(v: Any) -> Any:
    if isinstance(v, float):
        return round(v, 8)
    if isinstance(v, tuple):
        return [_round_num(x) for x in v]
    if isinstance(v, list):
        return [_round_num(x) for x in v]
    if isinstance(v, dict):
        return {k: _round_num(x) for k, x in v.items()}
    return v


def _close(a: Any, b: Any, atol: float) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= atol
    if isinstance(a, dict) and isinstance(b, dict):
        keys = set(a) | set(b)
        return all(_close(a.get(k), b.get(k), atol) for k in keys if k != "reason")
    try:
        aa = list(a)  # type: ignore[arg-type]
        bb = list(b)  # type: ignore[arg-type]
    except TypeError:
        return a == b
    if len(aa) != len(bb):
        return False
    return all(_close(x, y, atol) for x, y in zip(aa, bb))


def _numpy_crosscheck(op: str, args: dict[str, Any], value: Any) -> dict[str, Any] | None:
    try:
        import numpy as np
    except ImportError:
        return None
    try:
        if op == "dot":
            got = float(np.dot(np.array(vec3(args.get("a"))), np.array(vec3(args.get("b")))))
            return {"backend": "numpy", "match": abs(got - float(value)) <= ATOL, "value": got}
        if op == "cross":
            got = tuple(float(x) for x in np.cross(np.array(vec3(args.get("a"))), np.array(vec3(args.get("b")))))
            return {"backend": "numpy", "match": _close(got, value, ATOL), "value": got}
        if op == "mat4_mul":
            A = np.array(mat4(args.get("a"))).reshape(4, 4)
            B = np.array(mat4(args.get("b"))).reshape(4, 4)
            got = tuple(float(x) for x in (A @ B).reshape(16))
            return {"backend": "numpy", "match": _close(got, value, 1e-4), "value": _round_num(got)}
    except Exception as exc:
        return {"backend": "numpy", "error": str(exc)[:200]}
    return None


def _compute(op: str, args: dict[str, Any]) -> Any:
    if op == "add":
        return add(args.get("a"), args.get("b"))
    if op == "sub":
        return sub(args.get("a"), args.get("b"))
    if op == "scale":
        return scale(args.get("a"), args.get("s", args.get("k", 1.0)))
    if op == "length":
        return length(args.get("a"))
    if op == "normalize":
        return normalize(args.get("a"))
    if op == "dot":
        return dot(args.get("a"), args.get("b"))
    if op == "cross":
        return cross(args.get("a"), args.get("b"))
    if op == "facing":
        return facing(args.get("view") or args.get("a"), args.get("normal") or args.get("b"),
                      eps=_num(args.get("eps"), 0.0))
    if op == "triangle_normal":
        return triangle_normal(args.get("v0"), args.get("v1"), args.get("v2"))
    if op == "quat_mul":
        return quat_mul(args.get("a") or args.get("q0"), args.get("b") or args.get("q1"))
    if op == "quat_normalize":
        return quat_normalize(args.get("q") or args.get("a"))
    if op == "quat_slerp":
        return quat_slerp(args.get("q0") or args.get("a"), args.get("q1") or args.get("b"),
                          args.get("t", 0.5))
    if op == "quat_from_axis_angle":
        return quat_from_axis_angle(args.get("axis"), args.get("angle"))
    if op == "quat_rotate":
        return quat_rotate(args.get("q") or args.get("a"), args.get("v") or args.get("b"))
    if op == "mat4_mul":
        return mat4_mul(args.get("a"), args.get("b"))
    if op == "mat4_transform":
        return mat4_transform(args.get("m") or args.get("a"), args.get("p") or args.get("b"),
                              _num(args.get("w"), 1.0))
    if op == "look_at":
        return look_at(args.get("eye"), args.get("center") or args.get("target"),
                       args.get("up", (0.0, 1.0, 0.0)))
    if op == "perspective":
        return perspective(args.get("fovy"), args.get("aspect"), args.get("near") or args.get("znear"),
                           args.get("far") or args.get("zfar"))
    if op == "ray_triangle":
        return ray_triangle(
            args.get("origin") or args.get("orig") or args.get("o"),
            args.get("direction") or args.get("dir") or args.get("d"),
            args.get("v0"), args.get("v1"), args.get("v2"),
            cull_backface=bool(args.get("cull_backface")),
        )
    if op == "ray_aabb":
        return ray_aabb(
            args.get("origin") or args.get("orig"),
            args.get("direction") or args.get("dir"),
            args.get("min") or args.get("bmin"),
            args.get("max") or args.get("bmax"),
        )
    raise ValueError(f"unknown op {op}")


def run_op(op: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Evaluate one named op. If ``claimed`` is set, report whether it matches."""
    name = (op or "").strip().lower().replace("-", "_")
    fields = dict(args or {})
    if name in ("catalog", "help", "ops"):
        return {"ok": True, "ops": list(OPS), "convention": "quat xyzw, mat4 row-major, OpenGL Y-up"}
    claimed = fields.pop("claimed", None)
    if isinstance(claimed, str) and not claimed.strip().startswith(("[", "{")):
        claimed = None  # prose note, not a numeric claim
    else:
        claimed = _maybe_parse(claimed)
    atol = _num(fields.pop("atol", ATOL), ATOL)
    fields = {k: _maybe_parse(v) for k, v in fields.items()}
    try:
        value = _compute(name, fields)
    except Exception as exc:
        return {"ok": False, "op": name, "error": str(exc)[:300]}
    out: dict[str, Any] = {
        "ok": True,
        "op": name,
        "value": _round_num(value),
        "backend": "stdlib",
    }
    np_ck = _numpy_crosscheck(name, fields, value)
    if np_ck:
        out["numpy"] = np_ck
    if claimed is not None:
        match = _close(value, claimed, atol)
        out["claimed"] = _round_num(claimed)
        out["match"] = match
        out["ok"] = True
        out["verdict"] = "pass" if match else "mismatch"
        if not match:
            out["hint"] = (
                "stdlib reference disagrees with claimed. Fix the formula before "
                "embedding it in C++/C#/GDScript."
            )
    return out


def run_action(action: str = "catalog", **fields: Any) -> dict[str, Any]:
    """Native-tool entry. ``action`` is an op name, or ``batch`` with ``ops``."""
    act = (action or "catalog").strip().lower().replace("-", "_")
    if act in ("batch", "run"):
        rows = fields.get("ops") or fields.get("items") or []
        if not isinstance(rows, list) or not rows:
            return {"ok": False, "error": "batch needs ops: [{op, ...}, ...]"}
        results = []
        all_ok = True
        for row in rows[:24]:
            if not isinstance(row, dict):
                results.append({"ok": False, "error": "op row must be an object"})
                all_ok = False
                continue
            op_name = str(row.get("op") or "")
            one = run_op(op_name, {k: v for k, v in row.items() if k != "op"})
            results.append(one)
            if not one.get("ok") or one.get("verdict") == "mismatch":
                all_ok = False
        return {"ok": all_ok, "results": results}
    return run_op(act, fields)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "catalog"):
        print(json.dumps(run_op("catalog"), indent=2))
        return 0
    raw = args[0]
    if raw.startswith("{"):
        payload = json.loads(raw)
        op = str(payload.pop("op", payload.pop("action", "catalog")))
        result = run_action(op, **payload)
    else:
        payload: dict[str, Any] = {}
        if len(args) > 1 and args[1].startswith("{"):
            payload = json.loads(args[1])
        result = run_action(raw, **payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
