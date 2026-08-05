from __future__ import annotations

import base64
import hashlib
import io
import itertools
import json
import logging
import math
import pathlib
import sys
from typing import Any

import numpy as np
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flask_v5.app import (
    CHALLENGES,
    GAP,
    GLYPH_FRAME_MARGIN,
    GLYPH_SLOT_MIN_COVERAGE,
    MEMORY_COUNTERS,
    M,
    N,
    RR,
    START,
    TILE,
    ChallengeStore,
    build_challenge,
    build_glyph_alpha,
    create_app,
    glyph_frame_margins,
    glyph_slot_coverages,
    has_leading_zero_bits,
)


class ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.counters: dict[str, int] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.values[key] = value
        self.ttls[key] = ttl

    def delete(self, key: str) -> None:
        self.values.pop(key, None)
        self.ttls.pop(key, None)

    def incr(self, key: str) -> int:
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    def ttl(self, key: str) -> int:
        return self.ttls.get(key, -2)

    def expire(self, key: str, ttl: int) -> None:
        self.ttls[key] = ttl

    def pipeline(self) -> "FakeRedisPipeline":
        return FakeRedisPipeline(self)


class FakeRedisPipeline:
    def __init__(self, redis_client: FakeRedis) -> None:
        self.redis = redis_client

    def __enter__(self) -> "FakeRedisPipeline":
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        return None

    def watch(self, _key: str) -> None:
        return None

    def unwatch(self) -> None:
        return None

    def multi(self) -> None:
        return None

    def execute(self) -> list[Any]:
        return []

    def get(self, key: str) -> str | None:
        return self.redis.get(key)

    def ttl(self, key: str) -> int:
        return self.redis.ttl(key)

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.redis.setex(key, ttl, value)

    def delete(self, key: str) -> None:
        self.redis.delete(key)


def decode_image(data_url: str) -> np.ndarray:
    raw = data_url.split(",", 1)[1]
    image = Image.open(io.BytesIO(base64.b64decode(raw))).convert("RGBA")
    return np.asarray(image, dtype=np.float32) / 255.0


def natural_events() -> list[dict[str, float | str]]:
    events: list[dict[str, float | str]] = []
    t = 0.0
    points = [
        (20, 20),
        (38, 28),
        (60, 54),
        (82, 46),
        (104, 78),
        (128, 74),
        (150, 112),
        (176, 104),
        (202, 140),
        (230, 132),
        (260, 168),
    ]
    events.append({"type": "down", "x": points[0][0], "y": points[0][1], "t": t})
    for idx, (x, y) in enumerate(points[1:], start=1):
        t += 155 if idx in {3, 7} else 95
        events.append({"type": "move", "x": x, "y": y, "t": t})
    t += 130
    events.append({"type": "up", "x": points[-1][0], "y": points[-1][1], "t": t})
    return events


def direct_user_events() -> list[dict[str, float | str]]:
    events: list[dict[str, float | str]] = []
    t = 0.0
    for drag in range(5):
        x0 = 24 + drag * 22
        y0 = 28 + drag * 18
        events.append({"type": "down", "x": x0, "y": y0, "t": t})
        for step in range(1, 5):
            t += 70
            events.append({"type": "move", "x": x0 + step * 19, "y": y0 + step * 13 + (step % 2) * 3, "t": t})
        t += 80
        events.append({"type": "up", "x": x0 + 76, "y": y0 + 55, "t": t})
        t += 90
    return events


def robotic_events() -> list[dict[str, float | str]]:
    events: list[dict[str, float | str]] = []
    t = 0.0
    for drag in range(5):
        x0 = 20 + drag * 20
        y0 = 20 + drag * 15
        events.append({"type": "down", "x": x0, "y": y0, "t": t})
        for step in range(1, 7):
            t += 80
            events.append({"type": "move", "x": x0 + step * 10, "y": y0 + step * 10, "t": t})
        t += 80
        events.append({"type": "up", "x": x0 + 60, "y": y0 + 60, "t": t})
        t += 80
    return events


def slot_center(slot: int) -> tuple[float, float]:
    col, row = slot % N, slot // N
    return START + col * (TILE + GAP) + TILE / 2, START + row * (TILE + GAP) + TILE / 2


def operation_events(challenge: dict[str, Any], target_mapping: list[int], robotic: bool = False) -> list[dict[str, float | str]]:
    events: list[dict[str, float | str]] = []
    tile_to_slot = {int(tile["id"]): int(tile["start_slot"]) for tile in challenge["tiles"]}
    t = 0.0
    guard = 0
    while [tile_to_slot[idx] for idx in range(M)] != target_mapping:
        tile_id = next(idx for idx in range(M) if tile_to_slot[idx] != target_mapping[idx])
        from_slot = tile_to_slot[tile_id]
        to_slot = target_mapping[tile_id]
        sx, sy = slot_center(from_slot)
        ex, ey = slot_center(to_slot)
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
        events.append({"type": "up", "x": ex, "y": ey, "t": t, "tile_id": tile_id, "from_slot": from_slot, "to_slot": to_slot})
        t += 80 if robotic else 120
        guard += 1
        assert guard <= M
    t += 220
    events.append({"type": "verify_click", "x": 0, "y": 0, "t": t, "target": "check"})
    return events


def issue(client: Any) -> tuple[dict[str, Any], list[int]]:
    response = client.post("/api/challenge")
    assert response.status_code == 200
    payload = response.get_json()
    challenge_id = payload["challenge_id"]
    answer = CHALLENGES[challenge_id].answer[:]
    return payload, answer


def issue_avoiding(client: Any, challenge_id_to_avoid: str) -> tuple[dict[str, Any], list[int]]:
    response = client.post("/api/challenge", json={"avoid_challenge_id": challenge_id_to_avoid})
    assert response.status_code == 200
    payload = response.get_json()
    challenge_id = payload["challenge_id"]
    answer = CHALLENGES[challenge_id].answer[:]
    return payload, answer


def solve_invisible_proofs(challenge: dict[str, Any]) -> list[dict[str, int]]:
    proofs = []
    for task in challenge["invisible_tasks"]:
        tile_id = int(task["tile_id"])
        salt = str(task["salt"])
        difficulty = int(task["difficulty"])
        for nonce in range(1_000_001):
            payload = f"{challenge['challenge_id']}:{tile_id}:{salt}:{nonce}".encode("utf-8")
            if has_leading_zero_bits(hashlib.sha256(payload).digest(), difficulty):
                proofs.append({"tile_id": tile_id, "nonce": nonce})
                break
        else:
            raise AssertionError("invisible proof was not solvable within the nonce limit")
    return proofs


def closest_inexact_mapping(answer: list[int]) -> list[int]:
    candidate = answer[:]
    first_tile = answer.index(0)
    second_tile = answer.index(N - 1)
    candidate[first_tile], candidate[second_tile] = candidate[second_tile], candidate[first_tile]
    return candidate


def min_glyph_slot_coverage(challenge_id: str) -> float:
    challenge = CHALLENGES[challenge_id]
    coverages = glyph_slot_coverages(build_glyph_alpha(challenge.glyph), challenge.global_phase)
    return min(coverages)


def min_glyph_frame_margin(challenge_id: str) -> int:
    challenge = CHALLENGES[challenge_id]
    margins = glyph_frame_margins(build_glyph_alpha(challenge.glyph), challenge.global_phase)
    return min(margins)


def visual_attack_mapping(challenge: dict[str, Any]) -> list[int]:
    tiles = [decode_image(tile["image"]) for tile in sorted(challenge["tiles"], key=lambda item: item["id"])]

    tile_signals = []
    for tile in tiles:
        signal = tile[:, :, :3].mean(axis=2)
        signal = np.clip(signal - np.percentile(signal, 45), 0.0, 1.0)
        norm = float(np.linalg.norm(signal))
        tile_signals.append(signal / norm if norm else signal)

    slot_cost = np.zeros((M, M), dtype=np.float32)
    if "hint_image" in challenge:
        hint = decode_image(challenge["hint_image"])
        hint_signal = hint[:, :, :3].mean(axis=2) * hint[:, :, 3]
        hint_image = Image.fromarray(np.uint8(np.clip(hint_signal * 255, 0, 255)))
        hint_image = hint_image.resize((N * RR, N * RR), Image.Resampling.BILINEAR)
        target = np.asarray(hint_image, dtype=np.float32) / 255.0
        for tile_id, signal in enumerate(tile_signals):
            for slot in range(M):
                x = (slot % N) * RR
                y = (slot // N) * RR
                target_slot = target[y : y + RR, x : x + RR]
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


def redis_ttl_check() -> dict[str, Any]:
    fake = FakeRedis()
    store = ChallengeStore(fake)
    challenge = build_challenge(ttl_seconds=12)
    store.save(challenge)
    key = next(iter(fake.ttls))
    loaded = store.get(challenge.challenge_id)
    return {
        "ttl_written": 1 <= fake.ttls[key] <= 12,
        "round_trip": loaded is not None and loaded.answer == challenge.answer,
    }


def shared_store_check() -> dict[str, Any]:
    fake = FakeRedis()
    issuer_store = ChallengeStore(fake)
    verifier_store = ChallengeStore(fake)
    challenge = build_challenge(ttl_seconds=30)
    issuer_store.save(challenge)

    passed = verifier_store.verify_attempt(
        challenge.challenge_id,
        lambda stored: {"passed": True, "reason": "passed", "status_code": 200, "fields": {"exact": stored.answer == challenge.answer}},
        failure_limit=5,
        attempt_limit=5,
    )
    replay = issuer_store.get(challenge.challenge_id)

    locked_challenge = build_challenge(ttl_seconds=30)
    issuer_store.save(locked_challenge)
    first = verifier_store.verify_attempt(
        locked_challenge.challenge_id,
        lambda _stored: {"passed": False, "reason": "wrong_mapping", "status_code": 200, "fields": {}},
        failure_limit=2,
        attempt_limit=2,
    )
    second = verifier_store.verify_attempt(
        locked_challenge.challenge_id,
        lambda _stored: {"passed": False, "reason": "wrong_mapping", "status_code": 200, "fields": {}},
        failure_limit=2,
        attempt_limit=2,
    )
    locked = issuer_store.get(locked_challenge.challenge_id)

    return {
        "cross_store_passed": passed["state"] == "passed",
        "one_time_deleted": replay is None,
        "failure_incremented": first["failures"] == 1 and first["attempts"] == 1,
        "locked_shared": second["state"] == "locked_failure" and locked is not None and locked.locked,
    }


def run() -> dict[str, Any]:
    CHALLENGES.clear()
    MEMORY_COUNTERS.clear()
    app = create_app(testing=True)
    app.logger.setLevel(logging.INFO)
    log_handler = ListHandler()
    app.logger.addHandler(log_handler)
    client = app.test_client()

    challenge, answer = issue(client)
    public_text = json.dumps(challenge, ensure_ascii=False)
    public_exposure = {
        "contains_answer_key": "answer" in challenge or "layout" in challenge or "token" in challenge,
        "contains_answer_value": json.dumps(answer) in public_text,
        "contains_hint_image": "hint_image" in challenge,
        "contains_ascii_hint_images": isinstance(challenge.get("hint_images"), list) and len(challenge["hint_images"]) == 2,
        "contains_target_glyph": "target_glyph" in challenge,
        "tile_count": len(challenge["tiles"]),
        "invisible_task_count": len(challenge.get("invisible_tasks", [])),
        "invisible_difficulty": challenge.get("invisible_tasks", [{}])[0].get("difficulty"),
        "has_challenge_id": bool(challenge["challenge_id"]),
    }
    first_glyph = CHALLENGES[challenge["challenge_id"]].glyph
    first_min_glyph_coverage = min_glyph_slot_coverage(challenge["challenge_id"])
    first_min_glyph_margin = min_glyph_frame_margin(challenge["challenge_id"])
    rotated_challenge, _rotated_answer = issue_avoiding(client, challenge["challenge_id"])
    rotated_glyph = CHALLENGES[rotated_challenge["challenge_id"]].glyph
    rotated_min_glyph_coverage = min_glyph_slot_coverage(rotated_challenge["challenge_id"])
    rotated_min_glyph_margin = min_glyph_frame_margin(rotated_challenge["challenge_id"])

    forged_score = client.post(
        "/api/verify",
        json={
            "challenge_id": challenge["challenge_id"],
            "mapping": answer,
            "events": [],
            "sb": 1,
            "dt": 2.0,
        },
    ).get_json()

    wrong_challenge, wrong_answer = issue(client)
    wrong_guess = list(range(M))
    if wrong_guess == wrong_answer:
        wrong_guess = list(reversed(wrong_guess))
    wrong_mapping = client.post(
        "/api/verify",
        json={
            "challenge_id": wrong_challenge["challenge_id"],
            "mapping": wrong_guess,
            "events": natural_events(),
        },
    ).get_json()

    challenge2, answer2 = issue(client)
    legitimate_events = operation_events(challenge2, answer2)
    legitimate = client.post(
        "/api/verify",
        json={
            "challenge_id": challenge2["challenge_id"],
            "mapping": answer2,
            "events": legitimate_events,
        },
    ).get_json()

    no_click_challenge, no_click_answer = issue(client)
    no_click_events = [event for event in operation_events(no_click_challenge, no_click_answer) if event["type"] != "verify_click"]
    no_click_correct = client.post(
        "/api/verify",
        json={
            "challenge_id": no_click_challenge["challenge_id"],
            "mapping": no_click_answer,
            "events": no_click_events,
        },
    ).get_json()

    replay = client.post(
        "/api/verify",
        json={
            "challenge_id": challenge2["challenge_id"],
            "mapping": answer2,
            "events": legitimate_events,
        },
    )

    challenge_direct, answer_direct = issue(client)
    direct_legitimate = client.post(
        "/api/verify",
        json={
            "challenge_id": challenge_direct["challenge_id"],
            "mapping": answer_direct,
            "events": operation_events(challenge_direct, answer_direct),
        },
    ).get_json()

    one_drag_challenge, _one_drag_answer = issue(client)
    one_drag_target = [int(tile["start_slot"]) for tile in one_drag_challenge["tiles"]]
    one_drag_target[0], one_drag_target[1] = one_drag_target[1], one_drag_target[0]
    CHALLENGES[one_drag_challenge["challenge_id"]].answer = one_drag_target[:]
    one_drag_legitimate = client.post(
        "/api/verify",
        json={
            "challenge_id": one_drag_challenge["challenge_id"],
            "mapping": one_drag_target,
            "events": operation_events(one_drag_challenge, one_drag_target),
        },
    ).get_json()

    session_challenge, session_answer = issue(client)
    session_mismatch = app.test_client().post(
        "/api/verify",
        json={
            "challenge_id": session_challenge["challenge_id"],
            "mapping": session_answer,
            "events": operation_events(session_challenge, session_answer),
        },
    ).get_json()

    forged_behavior_challenge, forged_behavior_answer = issue(client)
    forged_behavior = client.post(
        "/api/verify",
        json={
            "challenge_id": forged_behavior_challenge["challenge_id"],
            "mapping": forged_behavior_answer,
            "events": direct_user_events(),
        },
    ).get_json()

    robot_challenge, robot_answer = issue(client)
    robotic_correct = client.post(
        "/api/verify",
        json={
            "challenge_id": robot_challenge["challenge_id"],
            "mapping": robot_answer,
            "events": operation_events(robot_challenge, robot_answer, robotic=True),
        },
    ).get_json()

    proof_exact_challenge, proof_exact_answer = issue(client)
    proof_exact = client.post(
        "/api/verify",
        json={
            "challenge_id": proof_exact_challenge["challenge_id"],
            "mapping": proof_exact_answer,
            "events": operation_events(proof_exact_challenge, proof_exact_answer, robotic=True),
            "invisible_proofs": solve_invisible_proofs(proof_exact_challenge),
        },
    ).get_json()

    soft_challenge, soft_answer = issue(client)
    soft_mapping = closest_inexact_mapping(soft_answer)
    soft_pass = client.post(
        "/api/verify",
        json={
            "challenge_id": soft_challenge["challenge_id"],
            "mapping": soft_mapping,
            "events": operation_events(soft_challenge, soft_mapping),
            "invisible_proofs": solve_invisible_proofs(soft_challenge),
        },
    ).get_json()

    no_proof_challenge, no_proof_answer = issue(client)
    no_proof_mapping = closest_inexact_mapping(no_proof_answer)
    soft_without_proof = client.post(
        "/api/verify",
        json={
            "challenge_id": no_proof_challenge["challenge_id"],
            "mapping": no_proof_mapping,
            "events": operation_events(no_proof_challenge, no_proof_mapping),
        },
    ).get_json()

    soft_robot_challenge, soft_robot_answer = issue(client)
    soft_robot_mapping = closest_inexact_mapping(soft_robot_answer)
    soft_robotic = client.post(
        "/api/verify",
        json={
            "challenge_id": soft_robot_challenge["challenge_id"],
            "mapping": soft_robot_mapping,
            "events": operation_events(soft_robot_challenge, soft_robot_mapping, robotic=True),
            "invisible_proofs": solve_invisible_proofs(soft_robot_challenge),
        },
    ).get_json()

    challenge3, _answer3 = issue(client)
    attack_mapping = visual_attack_mapping(challenge3)
    visual_attack = client.post(
        "/api/verify",
        json={
            "challenge_id": challenge3["challenge_id"],
            "mapping": attack_mapping,
            "events": natural_events(),
        },
    ).get_json()

    MEMORY_COUNTERS.clear()
    locked_app = create_app(
        testing=True,
        config={
            "MAX_FAILURES_PER_CHALLENGE": 2,
            "RATE_LIMITS": {"challenge": 50, "verify": 50},
        },
    )
    locked_client = locked_app.test_client()
    locked_challenge, locked_answer = issue(locked_client)
    locked_wrong1 = locked_answer[1:] + locked_answer[:1]
    locked_wrong2 = locked_answer[2:] + locked_answer[:2]
    first_failure = locked_client.post(
        "/api/verify",
        json={
            "challenge_id": locked_challenge["challenge_id"],
            "mapping": locked_wrong1,
            "events": natural_events(),
        },
    ).get_json()
    second_failure = locked_client.post(
        "/api/verify",
        json={
            "challenge_id": locked_challenge["challenge_id"],
            "mapping": locked_wrong2,
            "events": natural_events(),
        },
    )
    locked_retry = locked_client.post(
        "/api/verify",
        json={
            "challenge_id": locked_challenge["challenge_id"],
            "mapping": locked_answer,
            "events": natural_events(),
        },
    )

    MEMORY_COUNTERS.clear()
    limited_app = create_app(
        testing=True,
        config={
            "RATE_LIMITS": {"challenge": 1, "verify": 50},
            "RATE_LIMIT_WINDOW_SECONDS": 60,
        },
    )
    limited_client = limited_app.test_client()
    rate_first = limited_client.post("/api/challenge")
    rate_second = limited_client.post("/api/challenge")

    MEMORY_COUNTERS.clear()
    spoof_limited_app = create_app(
        testing=True,
        config={
            "RATE_LIMITS": {"challenge": 1, "verify": 50},
            "RATE_LIMIT_WINDOW_SECONDS": 60,
            "TRUSTED_PROXIES": [],
        },
    )
    spoof_limited_client = spoof_limited_app.test_client()
    spoof_first = spoof_limited_client.post("/api/challenge", headers={"X-Forwarded-For": "203.0.113.10"})
    spoof_second = spoof_limited_client.post("/api/challenge", headers={"X-Forwarded-For": "203.0.113.11"})

    MEMORY_COUNTERS.clear()
    trusted_proxy_app = create_app(
        testing=True,
        config={
            "RATE_LIMITS": {"challenge": 1, "verify": 50},
            "RATE_LIMIT_WINDOW_SECONDS": 60,
            "TRUSTED_PROXIES": ["127.0.0.1"],
        },
    )
    trusted_proxy_client = trusted_proxy_app.test_client()
    trusted_proxy_client_2 = trusted_proxy_app.test_client()
    trusted_first = trusted_proxy_client.post("/api/challenge", headers={"X-Forwarded-For": "203.0.113.20"})
    trusted_second = trusted_proxy_client_2.post("/api/challenge", headers={"X-Forwarded-For": "203.0.113.21"})

    redis_ttl = redis_ttl_check()
    shared_store = shared_store_check()
    audit_logs = [message for message in log_handler.messages if message.startswith("audit ")]
    health_headers = client.get("/api/health").headers

    leaked_verify_keys = {"exact", "human", "interaction", "session", "score", "failures", "attempts"}

    assertions = {
        "public_payload_hides_answer": not public_exposure["contains_answer_key"] and not public_exposure["contains_answer_value"],
        "public_payload_omits_hint_image": not public_exposure["contains_hint_image"],
        "public_payload_includes_ascii_neighbor_hints": public_exposure["contains_ascii_hint_images"],
        "public_payload_omits_target_glyph": not public_exposure["contains_target_glyph"],
        "one_invisible_task_per_tile": public_exposure["invisible_task_count"] == M,
        "invisible_proof_cost_hardened": public_exposure["invisible_difficulty"] == 16,
        "avoid_challenge_rotates_glyph": first_glyph != rotated_glyph,
        "each_grid_cell_contains_glyph_part": first_min_glyph_coverage >= GLYPH_SLOT_MIN_COVERAGE
        and rotated_min_glyph_coverage >= GLYPH_SLOT_MIN_COVERAGE,
        "assembled_grid_contains_complete_glyph": first_min_glyph_margin >= GLYPH_FRAME_MARGIN
        and rotated_min_glyph_margin >= GLYPH_FRAME_MARGIN,
        "forged_score_rejected": forged_score["pass"] is False and forged_score["reason"] == "verification_failed",
        "wrong_mapping_rejected": wrong_mapping["pass"] is False and wrong_mapping["reason"] == "verification_failed",
        "verify_response_does_not_leak_oracle_fields": not leaked_verify_keys.intersection(forged_score)
        and not leaked_verify_keys.intersection(wrong_mapping)
        and not leaked_verify_keys.intersection(legitimate),
        "legitimate_server_known_solution_passes": legitimate == {"pass": True},
        "correct_solution_without_verify_click_rejected": no_click_correct == {"pass": False, "reason": "verification_failed"},
        "direct_user_solution_passes": direct_legitimate == {"pass": True},
        "one_drag_user_solution_passes": one_drag_legitimate == {"pass": True},
        "cross_session_solution_rejected": session_mismatch == {"pass": False, "reason": "verification_failed"},
        "unbound_synthetic_behavior_rejected": forged_behavior == {"pass": False, "reason": "verification_failed"},
        "robotic_correct_solution_rejected": robotic_correct == {"pass": False, "reason": "verification_failed"},
        "full_invisible_proof_prevents_exact_false_reject": proof_exact == {"pass": True},
        "near_solution_soft_passes": soft_pass == {"pass": True},
        "soft_pass_requires_all_invisible_proofs": soft_without_proof == {"pass": False, "reason": "verification_failed"},
        "soft_pass_requires_human_trajectory": soft_robotic == {"pass": False, "reason": "verification_failed"},
        "replay_rejected": replay.status_code == 404 or replay.get_json()["pass"] is False,
        "visual_attack_rejected": visual_attack["pass"] is False,
        "failure_counter_locks_challenge": first_failure == {"pass": False, "reason": "verification_failed"}
        and second_failure.status_code == 429
        and second_failure.get_json()["reason"] == "too_many_attempts"
        and locked_retry.status_code == 429,
        "rate_limit_rejects_burst": rate_first.status_code == 200
        and rate_second.status_code == 429
        and rate_second.get_json()["reason"] == "rate_limited"
        and "Retry-After" in rate_second.headers,
        "xff_spoof_cannot_bypass_untrusted_limit": spoof_first.status_code == 200
        and spoof_second.status_code == 429
        and spoof_second.get_json()["reason"] == "rate_limited",
        "trusted_proxy_xff_is_honored": trusted_first.status_code == 200 and trusted_second.status_code == 200,
        "redis_store_uses_ttl": redis_ttl["ttl_written"] and redis_ttl["round_trip"],
        "shared_store_prevents_worker_state_split": all(shared_store.values()),
        "audit_logs_written": any("challenge_issued" in message for message in audit_logs)
        and any("verify_failed" in message for message in audit_logs)
        and any("verify_passed" in message for message in audit_logs),
        "security_headers_present": health_headers.get("X-Frame-Options") == "DENY"
        and health_headers.get("X-Content-Type-Options") == "nosniff"
        and "frame-ancestors 'none'" in health_headers.get("Content-Security-Policy", ""),
    }

    return {
        "public_exposure": public_exposure,
        "glyph_slot_coverage": {
            "first_min": first_min_glyph_coverage,
            "rotated_min": rotated_min_glyph_coverage,
            "threshold": GLYPH_SLOT_MIN_COVERAGE,
        },
        "glyph_frame_margin": {
            "first_min": first_min_glyph_margin,
            "rotated_min": rotated_min_glyph_margin,
            "threshold": GLYPH_FRAME_MARGIN,
        },
        "forged_score": forged_score,
        "wrong_mapping": wrong_mapping,
        "legitimate": legitimate,
        "no_click_correct": no_click_correct,
        "direct_legitimate": direct_legitimate,
        "one_drag_legitimate": one_drag_legitimate,
        "session_mismatch": session_mismatch,
        "forged_behavior": forged_behavior,
        "robotic_correct": robotic_correct,
        "proof_exact": proof_exact,
        "soft_pass": soft_pass,
        "soft_without_proof": soft_without_proof,
        "soft_robotic": soft_robotic,
        "replay_status": replay.status_code,
        "visual_attack": {
            "mapping": attack_mapping,
            "pass": visual_attack["pass"],
            "reason": visual_attack["reason"],
        },
        "failure_lock": {
            "first": first_failure,
            "second_status": second_failure.status_code,
            "second": second_failure.get_json(),
            "locked_retry_status": locked_retry.status_code,
        },
        "rate_limit": {
            "first_status": rate_first.status_code,
            "second_status": rate_second.status_code,
            "second": rate_second.get_json(),
            "retry_after_header": rate_second.headers.get("Retry-After"),
        },
        "xff_spoof_limit": {
            "first_status": spoof_first.status_code,
            "second_status": spoof_second.status_code,
            "second": spoof_second.get_json(),
        },
        "trusted_proxy_xff": {
            "first_status": trusted_first.status_code,
            "second_status": trusted_second.status_code,
        },
        "redis_ttl": redis_ttl,
        "shared_store": shared_store,
        "security_headers": {
            "x_frame_options": health_headers.get("X-Frame-Options"),
            "x_content_type_options": health_headers.get("X-Content-Type-Options"),
            "referrer_policy": health_headers.get("Referrer-Policy"),
            "content_security_policy": health_headers.get("Content-Security-Policy"),
        },
        "audit_log_count": len(audit_logs),
        "assertions": assertions,
        "ok": all(assertions.values()),
    }


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ok"] else 1)
