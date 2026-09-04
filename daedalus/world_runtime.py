"""Campaign runtime — save, quests, dialogue, level graph.

Empty ``world/*.json`` is not a campaign. This module loads the kit, rejects
hollow graphs, and proves a slice: talk → flag → quest → travel.
Python is the source of truth. ``src/world/runtime.js`` is a game-loop copy.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SAVE_VERSION = 1


def empty_save(*, region: str = "hearth", act: str = "act-1") -> dict[str, Any]:
    return {
        "version": SAVE_VERSION,
        "region": region,
        "act": act,
        "flags": {},
        "inventory": [],
        "quests": {},
    }


def migrate_save(raw: Any, *, start: str = "hearth", act: str = "act-1") -> dict[str, Any]:
    """Accept v0 ``{region, flags}`` and fill the current document."""
    base = empty_save(region=start, act=act)
    if not isinstance(raw, dict):
        return base
    out = dict(base)
    try:
        out["version"] = int(raw.get("version") or 0) or SAVE_VERSION
    except (TypeError, ValueError):
        out["version"] = SAVE_VERSION
    region = str(raw.get("region") or start).strip() or start
    out["region"] = region
    out["act"] = str(raw.get("act") or act).strip() or act
    flags = raw.get("flags") if isinstance(raw.get("flags"), dict) else {}
    out["flags"] = {str(k): bool(v) for k, v in flags.items()}
    inv = raw.get("inventory")
    if isinstance(inv, list):
        out["inventory"] = [str(x) for x in inv if str(x).strip()]
    quests = raw.get("quests") if isinstance(raw.get("quests"), dict) else {}
    out["quests"] = {str(k): str(v) for k, v in quests.items()}
    return out


def validate_save(doc: dict[str, Any], kit: dict[str, Any] | None = None) -> list[str]:
    errors: list[str] = []
    if not isinstance(doc, dict):
        return ["save is not an object"]
    if int(doc.get("version") or 0) < 1:
        errors.append("save.version must be >= 1")
    if not str(doc.get("region") or "").strip():
        errors.append("save.region is empty")
    if not isinstance(doc.get("flags"), dict):
        errors.append("save.flags must be an object")
    if not isinstance(doc.get("inventory"), list):
        errors.append("save.inventory must be a list")
    if kit:
        regions = kit.get("regions") or {}
        if doc.get("region") not in regions:
            errors.append(f"save.region {doc.get('region')!r} is not on the map")
    return errors


def _as_str_list(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(x) for x in raw if str(x).strip()]
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    return []


def load_kit(root: Path | str) -> tuple[dict[str, Any] | None, str]:
    base = Path(root)
    map_path = base / "world" / "map.json"
    graph_path = base / "levels" / "graph.json"
    quests_path = base / "world" / "quests.json"
    if not map_path.is_file():
        return None, "missing world/map.json"
    try:
        mapping = json.loads(map_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"map.json: {exc}"[:160]
    if not isinstance(mapping, dict):
        return None, "map.json must be an object"

    regions: dict[str, dict[str, Any]] = {}
    for row in mapping.get("regions") or []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        rid = str(row["id"])
        regions[rid] = {
            "id": rid,
            "name": str(row.get("name") or rid),
            "exits": _as_str_list(row.get("exits")),
            "require_flags": _as_str_list(row.get("require_flags")),
        }
    start = str(mapping.get("start") or next(iter(regions), "") or "hearth")

    graph: dict[str, Any] = {"acts": [], "edges": []}
    if graph_path.is_file():
        try:
            raw_g = json.loads(graph_path.read_text(encoding="utf-8"))
            if isinstance(raw_g, dict):
                graph = raw_g
        except (OSError, json.JSONDecodeError) as exc:
            return None, f"graph.json: {exc}"[:160]
    edges: list[dict[str, Any]] = []
    for edge in graph.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        frm, to = str(edge.get("from") or ""), str(edge.get("to") or "")
        if not frm or not to:
            continue
        edges.append({
            "from": frm,
            "to": to,
            "require_flags": _as_str_list(edge.get("require_flags")),
            "autosave": bool(edge.get("autosave", True)),
        })
    acts = [a for a in (graph.get("acts") or []) if isinstance(a, dict)]
    act_id = str((acts[0] or {}).get("id") or graph.get("start") or "act-1") if acts else "act-1"

    quests: list[dict[str, Any]] = []
    if quests_path.is_file():
        try:
            raw_q = json.loads(quests_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return None, f"quests.json: {exc}"[:160]
        rows = raw_q.get("quests") if isinstance(raw_q, dict) else raw_q
        if isinstance(rows, list):
            for q in rows:
                if not isinstance(q, dict) or not q.get("id"):
                    continue
                when = q.get("complete_when") if isinstance(q.get("complete_when"), dict) else {}
                quests.append({
                    "id": str(q["id"]),
                    "title": str(q.get("title") or q["id"]),
                    "start_flags": _as_str_list(q.get("start_flags")),
                    "complete_when": _as_str_list(when.get("flags") or q.get("complete_when")),
                    "reward_flags": _as_str_list(q.get("reward_flags")),
                    "reward_items": _as_str_list(q.get("reward_items")),
                    "giver": str(q.get("giver") or ""),
                })

    dialogues: dict[str, dict[str, Any]] = {}
    char_dir = base / "characters"
    if char_dir.is_dir():
        for path in sorted(char_dir.glob("*.dialogue.json")):
            try:
                raw_d = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(raw_d, dict):
                continue
            did = str(raw_d.get("id") or path.name.replace(".dialogue.json", ""))
            nodes: dict[str, dict[str, Any]] = {}
            for node in raw_d.get("nodes") or []:
                if not isinstance(node, dict) or not node.get("id"):
                    continue
                choices = []
                for ch in node.get("choices") or []:
                    if not isinstance(ch, dict):
                        continue
                    nxt = ch.get("next")
                    choices.append({
                        "text": str(ch.get("text") or ""),
                        "next": None if nxt in (None, "", False) else str(nxt),
                        "set_flags": _as_str_list(ch.get("set_flags")),
                    })
                nodes[str(node["id"])] = {
                    "id": str(node["id"]),
                    "speaker": str(node.get("speaker") or did),
                    "line": str(node.get("line") or ""),
                    "choices": choices,
                }
            dialogues[did] = {
                "id": did,
                "start": str(raw_d.get("start") or next(iter(nodes), "")),
                "nodes": nodes,
            }

    kit = {
        "start": start,
        "act": act_id,
        "regions": regions,
        "acts": acts,
        "edges": edges,
        "quests": quests,
        "dialogues": dialogues,
        "root": str(base),
    }
    return kit, ""


def validate_kit(kit: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    regions: dict[str, Any] = kit.get("regions") or {}
    start = kit.get("start")
    if not regions:
        errors.append("map has no regions")
    elif start not in regions:
        errors.append(f"map.start {start!r} is not a region")
    for rid, region in regions.items():
        for dest in region.get("exits") or []:
            if dest not in regions:
                errors.append(f"region {rid} exits to unknown {dest!r}")
    for edge in kit.get("edges") or []:
        if edge.get("from") not in regions:
            errors.append(f"graph edge from unknown {edge.get('from')!r}")
        if edge.get("to") not in regions:
            errors.append(f"graph edge to unknown {edge.get('to')!r}")
    if not kit.get("quests"):
        errors.append("no quests — world/quests.json is empty")
    dialogues: dict[str, Any] = kit.get("dialogues") or {}
    if not dialogues:
        errors.append("no dialogue graphs — characters/*.dialogue.json missing")
    flag_setters = 0
    for did, dlg in dialogues.items():
        nodes = dlg.get("nodes") or {}
        if dlg.get("start") not in nodes:
            errors.append(f"dialogue {did} start is missing")
        for nid, node in nodes.items():
            if not str(node.get("line") or "").strip():
                errors.append(f"dialogue {did} node {nid} has no line")
            for ch in node.get("choices") or []:
                if ch.get("set_flags"):
                    flag_setters += 1
                nxt = ch.get("next")
                if nxt and nxt not in nodes:
                    errors.append(f"dialogue {did} choice next {nxt!r} missing")
    if dialogues and flag_setters < 1:
        errors.append("dialogue choices never set a flag")
    return errors


def _has_flags(save: dict[str, Any], needed: list[str]) -> bool:
    flags = save.get("flags") or {}
    return all(flags.get(name) for name in needed)


def _edge(kit: dict[str, Any], frm: str, to: str) -> dict[str, Any] | None:
    for edge in kit.get("edges") or []:
        if edge.get("from") == frm and edge.get("to") == to:
            return edge
    return None


def can_travel(kit: dict[str, Any], save: dict[str, Any], dest: str) -> tuple[bool, str]:
    cur = str(save.get("region") or "")
    dest = str(dest)
    if dest == cur:
        return True, "already there"
    regions: dict[str, Any] = kit.get("regions") or {}
    if dest not in regions:
        return False, f"unknown region {dest}"
    here = regions.get(cur) or {}
    listed = dest in (here.get("exits") or [])
    edge = _edge(kit, cur, dest)
    if not listed and edge is None:
        return False, f"no exit {cur} → {dest}"
    need = list(regions[dest].get("require_flags") or [])
    if edge:
        need.extend(edge.get("require_flags") or [])
    uniq: list[str] = []
    for flag in need:
        if flag not in uniq:
            uniq.append(flag)
    missing = [f for f in uniq if not (save.get("flags") or {}).get(f)]
    if missing:
        return False, "need flags: " + ", ".join(missing)
    return True, "ok"


def travel(kit: dict[str, Any], save: dict[str, Any], dest: str) -> tuple[dict[str, Any], str]:
    ok, detail = can_travel(kit, save, dest)
    if not ok:
        return save, detail
    save = dict(save)
    save["flags"] = dict(save.get("flags") or {})
    save["region"] = dest
    for act in kit.get("acts") or []:
        if dest in (act.get("levels") or []):
            save["act"] = str(act.get("id") or save.get("act"))
            break
    return save, "ok"


def advance_quests(kit: dict[str, Any], save: dict[str, Any]) -> dict[str, Any]:
    save = dict(save)
    flags = dict(save.get("flags") or {})
    quests_state = dict(save.get("quests") or {})
    inventory = list(save.get("inventory") or [])
    changed = True
    while changed:
        changed = False
        for quest in kit.get("quests") or []:
            qid = quest["id"]
            if quests_state.get(qid) == "complete":
                continue
            if not _has_flags({"flags": flags}, quest.get("start_flags") or []):
                quests_state[qid] = "locked"
                continue
            quests_state[qid] = "active"
            if _has_flags({"flags": flags}, quest.get("complete_when") or []):
                quests_state[qid] = "complete"
                for flag in quest.get("reward_flags") or []:
                    if not flags.get(flag):
                        flags[flag] = True
                        changed = True
                for item in quest.get("reward_items") or []:
                    if item not in inventory:
                        inventory.append(item)
    save["flags"] = flags
    save["quests"] = quests_state
    save["inventory"] = inventory
    return save


def _set_flags(save: dict[str, Any], names: list[str]) -> dict[str, Any]:
    save = dict(save)
    flags = dict(save.get("flags") or {})
    for name in names:
        flags[str(name)] = True
    save["flags"] = flags
    return save


def talk_start(kit: dict[str, Any], who: str) -> tuple[dict[str, Any] | None, str]:
    dlg = (kit.get("dialogues") or {}).get(who)
    if not dlg:
        return None, f"no dialogue {who}"
    node = (dlg.get("nodes") or {}).get(dlg.get("start"))
    if not node:
        return None, f"dialogue {who} has no start node"
    return node, ""


def apply_choice(
    kit: dict[str, Any],
    save: dict[str, Any],
    who: str,
    pick: int | str,
    *,
    node_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None, str]:
    dlg = (kit.get("dialogues") or {}).get(who)
    if not dlg:
        return save, None, f"no dialogue {who}"
    nid = node_id or str(dlg.get("start") or "")
    node = (dlg.get("nodes") or {}).get(nid)
    if not node:
        return save, None, f"node {nid} missing"
    choices = node.get("choices") or []
    chosen = None
    if isinstance(pick, int):
        if pick < 0 or pick >= len(choices):
            return save, node, f"choice {pick} out of range"
        chosen = choices[pick]
    else:
        needle = str(pick).strip().lower()
        for ch in choices:
            if needle and needle in str(ch.get("text") or "").lower():
                chosen = ch
                break
        if chosen is None:
            return save, node, f"no choice matching {pick!r}"
    save = _set_flags(save, chosen.get("set_flags") or [])
    save = advance_quests(kit, save)
    nxt = chosen.get("next")
    if not nxt:
        return save, None, "ended"
    nxt_node = (dlg.get("nodes") or {}).get(nxt)
    return save, nxt_node, "ok"


def flags_from_dialogue(kit: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    for dlg in (kit.get("dialogues") or {}).values():
        for node in (dlg.get("nodes") or {}).values():
            for ch in node.get("choices") or []:
                found.update(ch.get("set_flags") or [])
    return found


def flags_from_quests(kit: dict[str, Any], have: set[str]) -> set[str]:
    reachable = set(have)
    changed = True
    while changed:
        changed = False
        for quest in kit.get("quests") or []:
            need = set(quest.get("complete_when") or [])
            start = set(quest.get("start_flags") or [])
            if start <= reachable and need <= reachable:
                for flag in quest.get("reward_flags") or []:
                    if flag not in reachable:
                        reachable.add(flag)
                        changed = True
    return reachable


def prove_slice(kit: dict[str, Any]) -> dict[str, Any]:
    """Talk through flag-setting choices, complete quests, then gated travel."""
    errors: list[str] = []
    save = empty_save(region=str(kit.get("start") or "hearth"), act=str(kit.get("act") or "act-1"))
    save = advance_quests(kit, save)
    locked: list[tuple[str, str, list[str]]] = []
    start = save["region"]
    regions: dict[str, Any] = kit.get("regions") or {}
    here = regions.get(start) or {}
    candidates = list(here.get("exits") or [])
    for edge in kit.get("edges") or []:
        if edge.get("from") == start and edge.get("to") not in candidates:
            candidates.append(edge["to"])
    for dest in candidates:
        need = list((regions.get(dest) or {}).get("require_flags") or [])
        edge = _edge(kit, start, dest)
        if edge:
            need.extend(edge.get("require_flags") or [])
        if need and not _has_flags(save, need):
            locked.append((start, dest, need))

    # Walk every dialogue, taking flag-setting choices first.
    for who, dlg in (kit.get("dialogues") or {}).items():
        nid = dlg.get("start")
        guard = 0
        while nid and guard < 32:
            guard += 1
            node = (dlg.get("nodes") or {}).get(nid)
            if not node:
                break
            choices = node.get("choices") or []
            flagged = next((i for i, ch in enumerate(choices) if ch.get("set_flags")), None)
            idx = flagged if flagged is not None else (0 if choices else None)
            if idx is None:
                break
            save, nxt, _detail = apply_choice(kit, save, who, idx, node_id=nid)
            nid = nxt["id"] if nxt else None

    reachable = flags_from_quests(kit, flags_from_dialogue(kit) | {
        k for k, v in (save.get("flags") or {}).items() if v
    })
    for frm, dest, need in locked:
        missing = [f for f in need if f not in reachable]
        if missing:
            errors.append(f"gated {frm}→{dest} needs {missing} but nothing sets them")
        probe = dict(save)
        probe["region"] = frm
        ok, detail = can_travel(kit, probe, dest)
        if not ok:
            errors.append(f"after dialogue/quests still cannot travel {frm}→{dest}: {detail}")

    if len(kit.get("regions") or {}) >= 2 and not locked and not (kit.get("edges") or []):
        errors.append("two regions but no gated edge — quest cannot change the map")

    return {
        "ok": not errors,
        "errors": errors,
        "save": save,
        "flags": save.get("flags") or {},
        "region": save.get("region"),
        "quests": save.get("quests") or {},
    }


def run_script(kit: dict[str, Any], steps: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    save = empty_save(region=str(kit.get("start") or "hearth"), act=str(kit.get("act") or "act-1"))
    save = advance_quests(kit, save)
    log: list[str] = []
    errors: list[str] = []
    node_id: str | None = None
    who = ""
    for i, step in enumerate(steps or []):
        op = str(step.get("op") or "")
        if op == "talk":
            who = str(step.get("who") or "")
            node, err = talk_start(kit, who)
            if err:
                errors.append(f"step {i}: {err}")
                break
            node_id = node["id"] if node else None
            log.append(f"talk {who}: {(node or {}).get('line', '')[:80]}")
            pick = step.get("pick")
            if pick is not None and node_id:
                save, nxt, detail = apply_choice(kit, save, who, pick, node_id=node_id)
                log.append(f"choose {pick}: {detail}")
                node_id = nxt["id"] if nxt else None
        elif op == "choose":
            pick = step.get("pick", 0)
            save, nxt, detail = apply_choice(kit, save, who, pick, node_id=node_id)
            log.append(f"choose {pick}: {detail}")
            node_id = nxt["id"] if nxt else None
        elif op == "travel":
            dest = str(step.get("to") or "")
            save, detail = travel(kit, save, dest)
            log.append(f"travel {dest}: {detail}")
            if detail != "ok" and detail != "already there":
                errors.append(f"step {i}: {detail}")
        elif op == "assert_cannot_travel":
            dest = str(step.get("to") or "")
            ok, detail = can_travel(kit, save, dest)
            if ok:
                errors.append(f"step {i}: expected blocked travel to {dest}")
            else:
                log.append(f"blocked {dest}: {detail}")
        elif op == "assert_flag":
            flag = str(step.get("flag") or "")
            if not (save.get("flags") or {}).get(flag):
                errors.append(f"step {i}: missing flag {flag}")
            else:
                log.append(f"flag {flag}")
        elif op == "assert_region":
            want = str(step.get("region") or "")
            if save.get("region") != want:
                errors.append(f"step {i}: region {save.get('region')!r} != {want!r}")
            else:
                log.append(f"region {want}")
        else:
            errors.append(f"step {i}: unknown op {op}")
    return {"ok": not errors, "save": save, "log": log, "errors": errors}


def default_hearth_script() -> list[dict[str, Any]]:
    return [
        {"op": "assert_cannot_travel", "to": "wilds"},
        {"op": "talk", "who": "keeper", "pick": "teach me"},
        {"op": "assert_flag", "flag": "gate_open"},
        {"op": "travel", "to": "wilds"},
        {"op": "assert_region", "region": "wilds"},
    ]


def prove_project(root: Path | str) -> dict[str, Any]:
    kit, err = load_kit(root)
    if err:
        return {"ok": False, "errors": [err], "runtime": False}
    issues = validate_kit(kit)
    if issues:
        return {"ok": False, "errors": issues, "runtime": False, "start": kit.get("start")}
    proved = prove_slice(kit)
    script = run_script(kit, default_hearth_script()) if "keeper" in (kit.get("dialogues") or {}) else {"ok": True, "errors": []}
    errors = list(proved.get("errors") or [])
    if not script.get("ok"):
        # Custom kits need not use the hearth demo script.
        if "keeper" in (kit.get("dialogues") or {}) and "open_gate" in {
            q.get("id") for q in (kit.get("quests") or [])
        }:
            errors.extend(script.get("errors") or [])
    ok = proved.get("ok") is True and not errors
    return {
        "ok": ok,
        "runtime": ok,
        "errors": errors,
        "save": proved.get("save"),
        "flags": proved.get("flags"),
        "quests": proved.get("quests"),
        "start": kit.get("start"),
        "regions": list((kit.get("regions") or {}).keys()),
        "dialogues": list((kit.get("dialogues") or {}).keys()),
    }


def run_action(action: str, slug: str, **fields: Any) -> dict[str, Any]:
    """Native tool entry — read-only against the kit (in-memory save)."""
    from . import playground as pg

    root = pg.project_dir(slug)
    if not root.is_dir():
        return {"ok": False, "error": f"no project {slug}"}
    action = (action or "status").strip().lower()
    if action == "status":
        from .world_kit import status as world_status
        st = world_status(root)
        proof = prove_project(root) if (root / "world" / "map.json").is_file() else {"ok": False, "errors": ["no map"]}
        return {"ok": True, "world": st, "prove": proof}
    kit, err = load_kit(root)
    if err:
        return {"ok": False, "error": err}
    if action == "prove":
        return prove_project(root)
    save = migrate_save(fields.get("save"), start=str(kit.get("start") or "hearth"), act=str(kit.get("act") or "act-1"))
    if action == "talk":
        who = str(fields.get("who") or "")
        pick = fields.get("pick")
        node, nerr = talk_start(kit, who)
        if nerr:
            return {"ok": False, "error": nerr, "save": save}
        if pick is None:
            return {"ok": True, "node": node, "save": save}
        save, nxt, detail = apply_choice(kit, save, who, pick if not str(pick).isdigit() else int(pick), node_id=node["id"] if node else None)
        return {"ok": detail in ("ok", "ended"), "node": nxt, "detail": detail, "save": save}
    if action == "travel":
        save, detail = travel(kit, save, str(fields.get("to") or ""))
        return {"ok": detail in ("ok", "already there"), "detail": detail, "save": save}
    if action == "script":
        steps = fields.get("steps")
        if isinstance(steps, str):
            try:
                steps = json.loads(steps)
            except json.JSONDecodeError:
                return {"ok": False, "error": "steps must be JSON"}
        if not isinstance(steps, list):
            steps = default_hearth_script()
        return run_script(kit, steps)
    return {"ok": False, "error": f"unknown action {action}"}


SAVE_SCHEMA: dict[str, Any] = {
    "$id": "daedalus/world/save",
    "title": "Daedalus campaign save",
    "type": "object",
    "required": ["version", "region", "flags", "inventory", "quests"],
    "properties": {
        "version": {"type": "integer", "minimum": 1},
        "region": {"type": "string"},
        "act": {"type": "string"},
        "flags": {"type": "object", "additionalProperties": {"type": "boolean"}},
        "inventory": {"type": "array", "items": {"type": "string"}},
        "quests": {"type": "object", "additionalProperties": {"type": "string"}},
    },
}


RUNTIME_JS = r"""
/** Campaign runtime (JS copy). Python daedalus.world_runtime is the source of truth. */
export function emptySave(region = 'hearth', act = 'act-1') {
  return { version: 1, region, act, flags: {}, inventory: [], quests: {} };
}

export function migrateSave(raw, start = 'hearth', act = 'act-1') {
  const base = emptySave(start, act);
  if (!raw || typeof raw !== 'object') return base;
  return {
    ...base,
    version: Number(raw.version) || 1,
    region: String(raw.region || start),
    act: String(raw.act || act),
    flags: { ...(raw.flags || {}) },
    inventory: Array.isArray(raw.inventory) ? raw.inventory.map(String) : [],
    quests: { ...(raw.quests || {}) },
  };
}

function hasFlags(save, needed) {
  const flags = save.flags || {};
  return (needed || []).every((name) => flags[name]);
}

export function canTravel(kit, save, dest) {
  const cur = save.region;
  if (dest === cur) return { ok: true, detail: 'already there' };
  const regions = kit.regions || {};
  if (!regions[dest]) return { ok: false, detail: 'unknown region ' + dest };
  const listed = (regions[cur]?.exits || []).includes(dest);
  const edge = (kit.edges || []).find((e) => e.from === cur && e.to === dest);
  if (!listed && !edge) return { ok: false, detail: `no exit ${cur} → ${dest}` };
  const need = [...(regions[dest].require_flags || []), ...((edge && edge.require_flags) || [])];
  const missing = need.filter((f) => !save.flags?.[f]);
  if (missing.length) return { ok: false, detail: 'need flags: ' + missing.join(', ') };
  return { ok: true, detail: 'ok' };
}

export function travel(kit, save, dest) {
  const gate = canTravel(kit, save, dest);
  if (!gate.ok) return { save, detail: gate.detail };
  return { save: { ...save, region: dest }, detail: 'ok' };
}

export function advanceQuests(kit, save) {
  const flags = { ...(save.flags || {}) };
  const quests = { ...(save.quests || {}) };
  const inventory = [...(save.inventory || [])];
  let changed = true;
  while (changed) {
    changed = false;
    for (const quest of kit.quests || []) {
      if (quests[quest.id] === 'complete') continue;
      if (!hasFlags({ flags }, quest.start_flags || [])) { quests[quest.id] = 'locked'; continue; }
      quests[quest.id] = 'active';
      if (hasFlags({ flags }, quest.complete_when || [])) {
        quests[quest.id] = 'complete';
        for (const f of quest.reward_flags || []) {
          if (!flags[f]) { flags[f] = true; changed = true; }
        }
        for (const item of quest.reward_items || []) {
          if (!inventory.includes(item)) inventory.push(item);
        }
      }
    }
  }
  return { ...save, flags, quests, inventory };
}

export function applyChoice(kit, save, who, pick, nodeId) {
  const dlg = (kit.dialogues || {})[who];
  if (!dlg) return { save, node: null, detail: 'no dialogue ' + who };
  const nid = nodeId || dlg.start;
  const node = (dlg.nodes || {})[nid];
  if (!node) return { save, node: null, detail: 'node missing' };
  const choices = node.choices || [];
  let chosen = null;
  if (typeof pick === 'number') chosen = choices[pick];
  else {
    const needle = String(pick).toLowerCase();
    chosen = choices.find((c) => (c.text || '').toLowerCase().includes(needle));
  }
  if (!chosen) return { save, node, detail: 'no choice' };
  const flags = { ...(save.flags || {}) };
  for (const f of chosen.set_flags || []) flags[f] = true;
  let nextSave = advanceQuests(kit, { ...save, flags });
  if (!chosen.next) return { save: nextSave, node: null, detail: 'ended' };
  return { save: nextSave, node: (dlg.nodes || {})[chosen.next], detail: 'ok' };
}

export function saveToStorage(save, key = 'daedalus_save_v1') {
  try { localStorage.setItem(key, JSON.stringify(save)); } catch (e) { /* ignore */ }
}

export function loadFromStorage(start = 'hearth', key = 'daedalus_save_v1') {
  try { return migrateSave(JSON.parse(localStorage.getItem(key) || 'null'), start); }
  catch (e) { return emptySave(start); }
}
"""


RUNTIME_GD = r'''extends RefCounted
## Campaign helper. Python daedalus.world_runtime is the source of truth.

static func empty_save(region: String = "hearth", act: String = "act-1") -> Dictionary:
	return {"version": 1, "region": region, "act": act, "flags": {}, "inventory": [], "quests": {}}
'''
