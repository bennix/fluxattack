#!/usr/bin/env python3
"""Authorized live defensive audit against local FLUX v5 at 127.0.0.1:5056."""

from __future__ import annotations

import base64
import hashlib
import io
import itertools
import json
import math
import statistics
import time
from pathlib import Path
from typing import Any
from urllib import error, request

import numpy as np
from PIL import Image

BASE = "http://127.0.0.1:5056"
OUT = Path(__file__).resolve().parents[1] / "output"
OUT.mkdir(parents=True, exist_ok=True)

M = 9
N = 3
RR = 64


class Session:
    def __init__(self) -> None:
        self.cookie = ""

    def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, dict[str, str], Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if self.cookie:
            headers["Cookie"] = self.cookie
        req = request.Request(BASE + path, data=data, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                hdrs = {k: v for k, v in resp.headers.items()}
                sc = resp.getheader("Set-Cookie")
                if sc and "viscap_sid=" in sc:
                    self.cookie = sc.split(";", 1)[0]
                payload = json.loads(raw.decode("utf-8")) if raw else {}
                return resp.status, hdrs, payload
        except error.HTTPError as exc:
            raw = exc.read()
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except json.JSONDecodeError:
                payload = {"raw": raw.decode("utf-8", errors="replace")}
            return exc.code, {k: v for k, v in exc.headers.items()}, payload


def has_leading_zero_bits(digest: bytes, difficulty: int) -> bool:
    whole_bytes, remaining_bits = divmod(difficulty, 8)
    if any(b != 0 for b in digest[:whole_bytes]):
        return False
    if remaining_bits == 0:
        return True
    return digest[whole_bytes] >> (8 - remaining_bits) == 0


def solve_invisible_proofs(challenge: dict[str, Any]) -> tuple[list[dict[str, int]], dict[str, Any]]:
    proofs: list[dict[str, int]] = []
    nonces: list[int] = []
    t0 = time.perf_counter()
    for task in challenge["invisible_tasks"]:
        tile_id = int(task["tile_id"])
        salt = str(task["salt"])
        difficulty = int(task["difficulty"])
        found = None
        for nonce in range(1_000_001):
            payload = f"{challenge['challenge_id']}:{tile_id}:{salt}:{nonce}".encode("utf-8")
            if has_leading_zero_bits(hashlib.sha256(payload).digest(), difficulty):
                found = nonce
                break
        if found is None:
            raise RuntimeError(f"PoW unsolved tile={tile_id}")
        nonces.append(found)
        proofs.append({"tile_id": tile_id, "nonce": found})
    elapsed = time.perf_counter() - t0
    return proofs, {
        "elapsed_s": round(elapsed, 4),
        "nonce_sum": int(sum(nonces)),
        "nonce_mean": round(float(statistics.mean(nonces)), 2),
        "nonce_max": int(max(nonces)),
        "difficulty": int(challenge["invisible_tasks"][0]["difficulty"]),
        "tasks": len(proofs),
    }


def decode_image(data_url: str) -> np.ndarray:
    raw = data_url.split(",", 1)[1]
    image = Image.open(io.BytesIO(base64.b64decode(raw))).convert("RGBA")
    return np.asarray(image, dtype=np.float32) / 255.0


def visual_attack_mapping(challenge: dict[str, Any]) -> list[int]:
    """Boundary + optional hint matching; no server secret."""
    tiles = [decode_image(tile["image"]) for tile in sorted(challenge["tiles"], key=lambda item: item["id"])]
    rr = int(tiles[0].shape[0])
    tile_signals = []
    for tile in tiles:
        signal = tile[:, :, :3].mean(axis=2)
        signal = np.clip(signal - np.percentile(signal, 45), 0.0, 1.0)
        norm = float(np.linalg.norm(signal))
        tile_signals.append(signal / norm if norm else signal)

    slot_cost = np.zeros((M, M), dtype=np.float32)
    hints = challenge.get("hint_images") or []
    if hints:
        hint = decode_image(hints[0]["image"])
        hint_signal = hint[:, :, :3].mean(axis=2) * hint[:, :, 3]
        hint_image = Image.fromarray(np.uint8(np.clip(hint_signal * 255, 0, 255)))
        hint_image = hint_image.resize((N * rr, N * rr), Image.Resampling.BILINEAR)
        target = np.asarray(hint_image, dtype=np.float32) / 255.0
        for tile_id, signal in enumerate(tile_signals):
            for slot in range(M):
                x = (slot % N) * rr
                y = (slot // N) * rr
                target_slot = target[y : y + rr, x : x + rr]
                target_slot = np.clip(target_slot - np.percentile(target_slot, 45), 0.0, 1.0)
                norm = float(np.linalg.norm(target_slot))
                if norm:
                    target_slot = target_slot / norm
                slot_cost[tile_id, slot] = -float(np.sum(signal * target_slot))

    right_cost = np.zeros((M, M), dtype=np.float32)
    down_cost = np.zeros((M, M), dtype=np.float32)
    for a, b in itertools.product(range(M), repeat=2):
        right_cost[a, b] = float(np.mean((tile_signals[a][:, -4:] - tile_signals[b][:, :4]) ** 2))
        down_cost[a, b] = float(np.mean((tile_signals[a][-4:, :] - tile_signals[b][:4, :]) ** 2))

    best_score = float("inf")
    best_slots = tuple(range(M))
    for slot_to_tile in itertools.permutations(range(M)):
        score = 0.0
        for slot, tile_id in enumerate(slot_to_tile):
            score += float(slot_cost[tile_id, slot])
        for y in range(N):
            for x in range(N):
                slot = y * N + x
                tile_id = slot_to_tile[slot]
                if x + 1 < N:
                    score += 0.35 * float(right_cost[tile_id, slot_to_tile[slot + 1]])
                if y + 1 < N:
                    score += 0.35 * float(down_cost[tile_id, slot_to_tile[slot + N]])
        if score < best_score:
            best_score = score
            best_slots = slot_to_tile

    mapping = [0] * M
    for slot, tile_id in enumerate(best_slots):
        mapping[tile_id] = slot
    return mapping


def slot_center(challenge: dict[str, Any], slot: int) -> tuple[float, float]:
    col, row = slot % N, slot // N
    board = challenge.get("board") or {}
    start = float(board.get("start", 18))
    tile = float(board.get("tile", 96))
    gap = float(board.get("gap", 8))
    return start + col * (tile + gap) + tile / 2, start + row * (tile + gap) + tile / 2


def operation_events(challenge: dict[str, Any], target_mapping: list[int], robotic: bool = False) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    tile_to_slot = {int(tile["id"]): int(tile["start_slot"]) for tile in challenge["tiles"]}
    t = 0.0
    guard = 0
    while [tile_to_slot[idx] for idx in range(M)] != target_mapping:
        tile_id = next(idx for idx in range(M) if tile_to_slot[idx] != target_mapping[idx])
        from_slot = tile_to_slot[tile_id]
        to_slot = target_mapping[tile_id]
        sx, sy = slot_center(challenge, from_slot)
        ex, ey = slot_center(challenge, to_slot)
        events.append({"type": "down", "x": sx, "y": sy, "t": t, "tile_id": tile_id, "from_slot": from_slot})
        steps = 6 if robotic else 7
        for step in range(1, steps + 1):
            u = step / steps
            if robotic:
                x = sx + (ex - sx) * u
                y = sy + (ey - sy) * u
                t += 80
            else:
                bend = math.sin(math.pi * u)
                x = sx + (ex - sx) * u + 18 * bend
                y = sy + (ey - sy) * u + (10 if step % 2 else -8) * bend
                t += 155 if step in {3, 6} else 75
            events.append({"type": "move", "x": round(x, 1), "y": round(y, 1), "t": t})
        t += 30 if robotic else 90
        events.append(
            {
                "type": "swap",
                "x": ex,
                "y": ey,
                "t": t,
                "tile_id": tile_id,
                "from_slot": from_slot,
                "to_slot": to_slot,
            }
        )
        other = next(idx for idx, slot in tile_to_slot.items() if slot == to_slot)
        tile_to_slot[tile_id] = to_slot
        tile_to_slot[other] = from_slot
        t += 30 if robotic else 80
        events.append(
            {
                "type": "up",
                "x": ex,
                "y": ey,
                "t": t,
                "tile_id": tile_id,
                "from_slot": from_slot,
                "to_slot": to_slot,
            }
        )
        t += 80 if robotic else 120
        guard += 1
        assert guard <= M
    t += 220
    events.append({"type": "verify_click", "x": 0, "y": 0, "t": t, "target": "check"})
    return events


def start_mapping(challenge: dict[str, Any]) -> list[int]:
    mapping = [0] * M
    for tile in challenge["tiles"]:
        mapping[int(tile["id"])] = int(tile["start_slot"])
    return mapping


def recon() -> dict[str, Any]:
    s = Session()
    code, headers, health = s.call("GET", "/api/health")
    home = request.urlopen(BASE + "/", timeout=30)
    home_headers = {k: v for k, v in home.headers.items()}
    html = home.read().decode("utf-8", errors="replace")
    c_code, c_headers, challenge = s.call("POST", "/api/challenge", {})
    return {
        "health_status": code,
        "health": health,
        "home_title": "FLUX" if "FLUX" in html else "unknown",
        "security_headers": {
            "X-Frame-Options": home_headers.get("X-Frame-Options") or headers.get("X-Frame-Options"),
            "X-Content-Type-Options": home_headers.get("X-Content-Type-Options") or headers.get("X-Content-Type-Options"),
            "Content-Security-Policy": (home_headers.get("Content-Security-Policy") or "")[:180],
            "Referrer-Policy": home_headers.get("Referrer-Policy"),
            "Server": home_headers.get("Server"),
        },
        "challenge_status": c_code,
        "challenge_keys": sorted(challenge.keys()) if isinstance(challenge, dict) else [],
        "expires_in": challenge.get("expires_in"),
        "tile_count": len(challenge.get("tiles", [])),
        "hint_count": len(challenge.get("hint_images", [])),
        "pow_difficulty": challenge.get("invisible_tasks", [{}])[0].get("difficulty"),
        "session_cookie_set": "viscap_sid=" in (c_headers.get("Set-Cookie") or s.cookie),
        "public_answer_fields_absent": not any(
            k in challenge for k in ("answer", "P", "glyph", "global_phase", "token", "target")
        ),
    }


def case_no_proof_wrong_map() -> dict[str, Any]:
    s = Session()
    _, _, ch = s.call("POST", "/api/challenge", {})
    mapping = start_mapping(ch)
    events = operation_events(ch, mapping, robotic=True)
    code, _, body = s.call(
        "POST",
        "/api/verify",
        {"challenge_id": ch["challenge_id"], "mapping": mapping, "events": events, "invisible_proofs": []},
    )
    return {"status": code, "body": body, "blocked": body.get("pass") is False}


def case_pow_only_wrong_map() -> dict[str, Any]:
    s = Session()
    _, _, ch = s.call("POST", "/api/challenge", {})
    mapping = start_mapping(ch)
    proofs, pow_meta = solve_invisible_proofs(ch)
    events = operation_events(ch, mapping, robotic=False)
    code, _, body = s.call(
        "POST",
        "/api/verify",
        {
            "challenge_id": ch["challenge_id"],
            "mapping": mapping,
            "events": events,
            "invisible_proofs": proofs,
        },
    )
    return {"status": code, "body": body, "blocked": body.get("pass") is False, "pow": pow_meta}


def case_cross_session() -> dict[str, Any]:
    a = Session()
    b = Session()
    _, _, ch = a.call("POST", "/api/challenge", {})
    proofs, _ = solve_invisible_proofs(ch)
    mapping = start_mapping(ch)
    events = operation_events(ch, mapping)
    code, _, body = b.call(
        "POST",
        "/api/verify",
        {
            "challenge_id": ch["challenge_id"],
            "mapping": mapping,
            "events": events,
            "invisible_proofs": proofs,
        },
    )
    return {"status": code, "body": body, "blocked": body.get("pass") is False}


def case_replay_after_fail() -> dict[str, Any]:
    s = Session()
    _, _, ch = s.call("POST", "/api/challenge", {})
    mapping = start_mapping(ch)
    events = operation_events(ch, mapping)
    first = s.call(
        "POST",
        "/api/verify",
        {"challenge_id": ch["challenge_id"], "mapping": mapping, "events": events, "invisible_proofs": []},
    )
    second = s.call(
        "POST",
        "/api/verify",
        {"challenge_id": ch["challenge_id"], "mapping": mapping, "events": events, "invisible_proofs": []},
    )
    return {
        "first_status": first[0],
        "first_body": first[2],
        "second_status": second[0],
        "second_body": second[2],
        "one_shot_or_lock_effective": first[2].get("pass") is False
        and (second[0] in {400, 404, 429} or second[2].get("pass") is False),
    }


def case_forged_client_score() -> dict[str, Any]:
    s = Session()
    _, _, ch = s.call("POST", "/api/challenge", {})
    mapping = start_mapping(ch)
    events = operation_events(ch, mapping)
    code, _, body = s.call(
        "POST",
        "/api/verify",
        {
            "challenge_id": ch["challenge_id"],
            "mapping": mapping,
            "events": events,
            "invisible_proofs": [],
            "score": 0.01,
            "risk": 0.0,
            "human": True,
        },
    )
    return {
        "status": code,
        "body": body,
        "blocked": body.get("pass") is False,
        "no_oracle_leak": set(body.keys()) <= {"pass", "reason"},
    }


def visual_campaign(trials: int = 20) -> dict[str, Any]:
    passes = 0
    reasons: dict[str, int] = {}
    pow_times: list[float] = []
    for i in range(trials):
        s = Session()
        _, _, ch = s.call("POST", "/api/challenge", {})
        mapping = visual_attack_mapping(ch)
        proofs, pow_meta = solve_invisible_proofs(ch)
        pow_times.append(float(pow_meta["elapsed_s"]))
        events = operation_events(ch, mapping, robotic=False)
        code, _, body = s.call(
            "POST",
            "/api/verify",
            {
                "challenge_id": ch["challenge_id"],
                "mapping": mapping,
                "events": events,
                "invisible_proofs": proofs,
            },
        )
        ok = bool(body.get("pass"))
        passes += int(ok)
        reason = str(body.get("reason") or ("pass" if ok else f"http_{code}"))
        reasons[reason] = reasons.get(reason, 0) + 1
        print(f"[visual {i+1}/{trials}] pass={ok} reason={reason} pow_s={pow_meta['elapsed_s']}", flush=True)
    return {
        "trials": trials,
        "passes": passes,
        "pass_rate": round(passes / trials, 4),
        "reasons": reasons,
        "pow_elapsed_mean_s": round(float(statistics.mean(pow_times)), 4),
        "pow_elapsed_max_s": round(max(pow_times), 4),
    }


def pow_benchmark(samples: int = 5) -> dict[str, Any]:
    rows = []
    for _ in range(samples):
        s = Session()
        _, _, ch = s.call("POST", "/api/challenge", {})
        _, meta = solve_invisible_proofs(ch)
        rows.append(meta)
    return {
        "samples": samples,
        "difficulty_bits": rows[0]["difficulty"],
        "tasks_per_challenge": rows[0]["tasks"],
        "elapsed_mean_s": round(float(statistics.mean(r["elapsed_s"] for r in rows)), 4),
        "elapsed_max_s": round(max(r["elapsed_s"] for r in rows), 4),
        "nonce_mean": round(float(statistics.mean(r["nonce_mean"] for r in rows)), 2),
        "rows": rows,
    }


def main() -> None:
    report: dict[str, Any] = {
        "target": BASE,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "scope": "authorized local defensive audit of FLUX v5 Flask CAPTCHA",
    }
    print("== recon ==", flush=True)
    report["recon"] = recon()
    print(json.dumps(report["recon"], ensure_ascii=False), flush=True)

    print("== pow benchmark ==", flush=True)
    report["pow_benchmark"] = pow_benchmark(5)
    print(json.dumps(report["pow_benchmark"], ensure_ascii=False), flush=True)

    print("== negative controls ==", flush=True)
    report["negative"] = {
        "no_proof_wrong_map": case_no_proof_wrong_map(),
        "pow_only_wrong_map": case_pow_only_wrong_map(),
        "cross_session": case_cross_session(),
        "replay_after_fail": case_replay_after_fail(),
        "forged_client_score": case_forged_client_score(),
    }
    print(json.dumps(report["negative"], ensure_ascii=False), flush=True)

    print("== visual+PoW campaign ==", flush=True)
    report["visual_attack_campaign"] = visual_campaign(20)

    neg = report["negative"]
    report["summary"] = {
        "answer_not_in_public_payload": report["recon"]["public_answer_fields_absent"],
        "security_headers_ok": report["recon"]["security_headers"].get("X-Frame-Options") == "DENY",
        "pow_solvable_client_side": True,
        "pow_mean_s": report["pow_benchmark"]["elapsed_mean_s"],
        "client_score_forgery_blocked": neg["forged_client_score"]["blocked"],
        "cross_session_blocked": neg["cross_session"]["blocked"],
        "one_shot_lock_ok": neg["replay_after_fail"]["one_shot_or_lock_effective"],
        "visual_plus_pow_pass_rate": report["visual_attack_campaign"]["pass_rate"],
        "broken": report["visual_attack_campaign"]["pass_rate"] >= 0.25,
    }
    out = OUT / "live_audit_results.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("WROTE", out, flush=True)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
