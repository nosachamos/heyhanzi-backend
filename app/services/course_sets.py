"""Authoritative, resumable character sets on the existing notebook trail.

Only checked answers advance a required question. Paper is explicit self-report.
The existing JSON run columns retain events; no notebook history is rewritten.
"""

import hashlib
import hmac
import json
import math
import re
import unicodedata
from contextvars import ContextVar

from app.services.learning import learning_error

signing_key = ContextVar("course_set_receipt_key", default=None)
receipt_scope = ContextVar("course_set_receipt_scope", default="learner")
UNASSESSED = "unverified-set-history"
PROTOCOL = "set-v1"


def is_set(item):
    return item["definition"].get("setActivity", {}).get("schema") in {
        "character-set-v1",
        "course-practice-v1",
    }


def validate_definition(item):
    definition = item["definition"]
    steps = definition["steps"]
    practice = definition["setActivity"]["schema"] == "course-practice-v1"
    if (
        not 1 <= len(steps) <= (128 if practice else 16)
        or len({s["id"] for s in steps}) != len(steps)
        or definition["setActivity"]["partIds"] != [s["id"] for s in steps]
    ):
        raise ValueError("Invalid character-set parts")
    for step in steps:
        if step["kind"] == "check":
            if len(step["questions"]) != 1:
                raise ValueError("One independently graded question per set part is required")
            q = step["questions"][0]
            if q["kind"] == "choice":
                if (
                    len(q["choices"]) < (2 if practice else 3)
                    or len({c["id"] for c in q["choices"]}) != len(q["choices"])
                    or sum(c["correct"] for c in q["choices"]) != 1
                ):
                    raise ValueError("Ambiguous character-set choice")
            elif practice and q["kind"] == "production":
                rubric = q.get("rubric", [])
                if (
                    q.get("response") not in {"written", "spoken"}
                    or not 1 <= len(rubric) <= 8
                    or len({r["id"] for r in rubric}) != len(rubric)
                    or any(
                        not re.fullmatch(r"[a-zA-Z0-9-]{1,24}", r["id"]) or not r.get("text")
                        for r in rubric
                    )
                    or not 1 <= q.get("minLength", 1) <= 4000
                ):
                    raise ValueError("Invalid communication practice rubric")
            elif q["kind"] != "text" or not q.get("accepted"):
                raise ValueError("Unsupported character-set question")
        elif practice and step["kind"] == "teach" and step.get("blocks"):
            continue
        elif step["kind"] != "stroke" or not step.get("reference", {}).get("medians"):
            raise ValueError("Unsupported character-set part")


def definition_for_run(item, run_id):
    definition = item["definition"]
    variants = definition.get("setActivity", {}).get("reinforcementVariants", [])
    if definition.get("setActivity", {}).get("formBank"):
        from app.services import course_assessments

        return course_assessments.materialize(
            item["id"], item["fingerprint"], course_assessments.form_index(run_id)
        )
    if not variants:
        return definition
    seed = 2166136261
    for char in run_id:
        seed = ((seed ^ ord(char)) * 16777619) & 0xFFFFFFFF
    selected = {s["id"]: s for s in variants[seed % len(variants)]}
    return {**definition, "steps": [selected.get(s["id"], s) for s in definition["steps"]]}


def events(run, step_id=None):
    return [
        a
        for a in run["attempts"]
        if len(a["answer"]) >= 3
        and a["answer"][0] == PROTOCOL
        and (step_id is None or a["answer"][1] == step_id)
    ]


def progress(item, run):
    steps = definition_for_run(item, run["id"])["steps"]
    completed = []
    for step in steps:
        if any(a["answer"][2] == "advance" for a in events(run, step["id"])):
            completed.append(step["id"])
        else:
            break
    return {
        "partIds": [s["id"] for s in steps],
        "completedPartIds": completed,
        "activePartId": steps[min(len(completed), len(steps) - 1)]["id"],
    }


def status(item, run):
    value = progress(item, run)
    return "completed" if len(value["completedPartIds"]) == len(value["partIds"]) else "draft"


def ready(step, run):
    values = events(run, step["id"])
    if step["kind"] == "teach":
        return True
    if step["kind"] == "stroke":
        return any(
            a["answer"][2] == "stroke" and (a.get("correct") is True or a.get("outcome") == "paper")
            for a in values
        )
    return all(
        any(
            a["answer"][2] == q["id"]
            and (
                a.get("correct") is True
                or q["kind"] == "production"
                and a.get("outcome") == "practiced"
            )
            for a in values
        )
        for q in step["questions"]
    )


def validate_draft(item, draft, run):
    if not draft:
        return
    if len(draft) != 2 or draft[0] != PROTOCOL or len(draft[1]) > 100_000:
        raise learning_error(422, "course_invalid_set_draft")
    try:
        value = json.loads(draft[1])
    except (ValueError, TypeError):
        raise learning_error(422, "course_invalid_set_draft") from None
    expected = progress(item, run)["activePartId"]
    if (
        not isinstance(value, dict)
        or value.get("stepId") != expected
        or not isinstance(value.get("questions", {}), dict)
    ):
        raise learning_error(422, "course_invalid_set_cursor")
    step = next(s for s in definition_for_run(item, run["id"])["steps"] if s["id"] == expected)
    allowed = {q["id"]: q for q in step.get("questions", [])}
    if set(value.get("questions", {})) - allowed.keys():
        raise learning_error(422, "course_invalid_set_draft")
    for id, field in value.get("questions", {}).items():
        q = allowed[id]
        modes = (
            {"hanzi", "speech", "unrecorded"}
            if q["kind"] == "production"
            else {"hanzi", "pinyin", "choice"}
        )
        if (
            not isinstance(field, dict)
            or field.get("mode") not in modes
            or not isinstance(field.get("value", ""), str)
            or len(field.get("value", "")) > (4000 if q["kind"] == "production" else 240)
            or not isinstance(field.get("supports", []), list)
            or len(field.get("supports", [])) > 20
            or any(not isinstance(v, str) or len(v) > 64 for v in field.get("supports", []))
        ):
            raise learning_error(422, "course_invalid_set_draft")
    writing = value.get("writing")
    if writing is not None:
        if (
            step["kind"] != "stroke"
            or not isinstance(writing, dict)
            or writing.get("mode") not in {"screen", "paper"}
        ):
            raise learning_error(422, "course_invalid_set_draft")
        if not isinstance(writing.get("round", 1), int) or not 1 <= writing.get("round", 1) <= len(
            step["reference"]["medians"]
        ):
            raise learning_error(422, "course_invalid_set_draft")
        ink = writing.get("ink", [])
        if (
            not isinstance(ink, list)
            or len(ink) > len(step["reference"]["medians"])
            or any(not valid_points(p) for p in ink)
        ):
            raise learning_error(422, "course_invalid_set_draft")


def normal(value, mode, rule="word"):
    value = unicodedata.normalize("NFC", value).strip()
    if mode == "pinyin":
        return re.sub(
            r"[\s'’，,。.!！?？;；:：]+", "", value.lower().replace("u:", "ü").replace("v", "ü")
        )
    value = re.sub(r"[。.！!？?，,]$" if rule == "word" else r"[。.]$", "", value).strip()
    return re.sub(r"\s*[。.,，\n]\s*", "|", value) if rule == "composition" else value


def valid_points(points):
    return (
        isinstance(points, list)
        and 2 <= len(points) <= 64
        and all(
            isinstance(p, list)
            and len(p) == 2
            and all(type(v) in {int, float} and math.isfinite(v) and -512 <= v <= 1536 for v in p)
            for p in points
        )
    )


def resample(points, count=32):
    lengths = [math.dist(a, b) for a, b in zip(points, points[1:])]
    total = sum(lengths)
    result = []
    index = 0
    covered = 0
    for n in range(count):
        target = total * n / (count - 1)
        while index < len(lengths) - 1 and covered + lengths[index] < target:
            covered += lengths[index]
            index += 1
        a, b = points[index], points[index + 1]
        fraction = min(1, (target - covered) / (lengths[index] or 1))
        result.append([a[0] + (b[0] - a[0]) * fraction, a[1] + (b[1] - a[1]) * fraction])
    return result


def matches_stroke(ink, median):
    length = sum(math.dist(a, b) for a, b in zip(ink, ink[1:]))
    expected = sum(math.dist(a, b) for a, b in zip(median, median[1:]))
    if not expected or not expected * 0.2 <= length <= expected * 3.5:
        return False
    forward = math.dist(ink[0], median[0]) + math.dist(ink[-1], median[-1])
    reverse = math.dist(ink[0], median[-1]) + math.dist(ink[-1], median[0])
    if (
        reverse + min(55, expected * 0.2) < forward
        or math.dist(ink[0], median[0]) > 220
        or math.dist(ink[-1], median[-1]) > 220
    ):
        return False
    gaps = [math.dist(a, b) for a, b in zip(resample(ink), resample(median))]
    return sum(gaps) / len(gaps) <= 160 and max(gaps) <= 280


def receipt(run, attempt):
    key = signing_key.get()
    if not key:
        raise learning_error(503, "course_signing_unavailable")
    body = [
        PROTOCOL,
        receipt_scope.get(),
        run["id"],
        run["instanceId"],
        run["fingerprint"],
        attempt["id"],
        attempt["answer"],
        attempt["supports"],
        attempt["correct"],
        attempt.get("outcome"),
        attempt["feedback"],
    ]
    return hmac.new(
        key.encode(),
        json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode(),
        hashlib.sha256,
    ).hexdigest()


def public_attempt(run, attempt):
    value = {**attempt}
    if (
        len(value["answer"]) == 5
        and value["answer"][:1] == [PROTOCOL]
        and value["answer"][2] == "stroke"
    ):
        try:
            data = json.loads(value["answer"][4])
        except ValueError:
            raise learning_error(422, "course_invalid_stroke_answer") from None
        if not isinstance(data, dict):
            raise learning_error(422, "course_invalid_stroke_answer")
        if "strokes" in data:
            data = {
                **data,
                "inkSha256": hashlib.sha256(
                    json.dumps(data["strokes"], separators=(",", ":")).encode()
                ).hexdigest(),
            }
            data.pop("strokes")
            value["answer"] = [*value["answer"][:4], json.dumps(data, separators=(",", ":"))]
    value["proof"] = receipt(run, value)
    return value


def authentic_history(run, attempt):
    proof = attempt.get("proof")
    return isinstance(proof, str) and hmac.compare_digest(receipt(run, attempt), proof)


def checked_draft(run):
    if len(run["draft"]) != 2 or run["draft"][0] != PROTOCOL:
        return run["draft"]
    value = json.loads(run["draft"][1])
    value.pop("retrying", None)
    value.pop("retryAtAttemptCount", None)
    return [PROTOCOL, json.dumps(value, separators=(",", ":"))]


def same_submission(run, previous, payload):
    if previous["answer"] == payload.answer:
        return True
    if (
        len(previous["answer"]) == len(payload.answer) == 5
        and previous["answer"][:4] == payload.answer[:4]
        and payload.answer[0] == PROTOCOL
        and payload.answer[2] == "stroke"
    ):
        candidate = {**previous, "answer": payload.answer}
        return public_attempt(run, previous)["answer"] == public_attempt(run, candidate)["answer"]
    return False


def grade(item, answer, run, *, attempt_id, supports, proof=None):
    limit = 2048 if item["definition"]["setActivity"]["schema"] == "course-practice-v1" else 256
    if len(run["attempts"]) >= limit:
        raise learning_error(409, "course_set_attempt_limit")
    if len(answer) < 3 or answer[0] != PROTOCOL:
        raise learning_error(422, "course_invalid_set_answer")
    active = progress(item, run)["activePartId"]
    steps = definition_for_run(item, run["id"])["steps"]
    if answer[1] != active:
        raise learning_error(409, "course_set_part_locked")
    step = next(s for s in steps if s["id"] == active)
    if answer[2] == "advance":
        if len(answer) != 3 or not ready(step, run):
            raise learning_error(409, "course_set_part_unfinished")
        position = next(n for n, s in enumerate(steps) if s["id"] == active)
        next_id = steps[min(position + 1, len(steps) - 1)]["id"]
        return (
            None,
            "Practice saved.",
            [PROTOCOL, json.dumps({"stepId": next_id, "questions": {}}, separators=(",", ":"))],
            "visited",
        )
    if step["kind"] == "stroke":
        if len(answer) != 5 or answer[2] != "stroke" or answer[3] not in {"drawn", "paper"}:
            raise learning_error(422, "course_invalid_stroke_answer")
        try:
            data = json.loads(answer[4])
        except ValueError:
            raise learning_error(422, "course_invalid_stroke_answer") from None
        if not isinstance(data, dict):
            raise learning_error(422, "course_invalid_stroke_answer")
        evidence = data.get("evidence")
        corrections = data.get("corrections", 0)
        if (
            type(corrections) is not int
            or not 0 <= corrections <= 10000
            or evidence not in {"guided", "recall", "supported-recall", "paper"}
        ):
            raise learning_error(422, "course_invalid_stroke_answer")
        if answer[3] == "paper":
            if evidence != "paper":
                raise learning_error(422, "course_invalid_stroke_answer")
            outcome = "paper"
            correct = None
            feedback = "Paper practice is self-reported. Handwriting is not checked."
        else:
            if (
                step.get("mode") == "recall"
                and evidence not in {"recall", "supported-recall"}
                or step.get("mode") != "recall"
                and evidence != "guided"
            ):
                raise learning_error(422, "course_invalid_stroke_answer")
            outcome = "correct"
            correct = True
            feedback = "Stroke order and direction checked."
            if proof:
                claimed = {
                    "id": attempt_id,
                    "answer": answer,
                    "supports": supports,
                    "correct": correct,
                    "outcome": outcome,
                    "feedback": feedback,
                }
                if not hmac.compare_digest(receipt(run, claimed), proof):
                    raise learning_error(422, "course_invalid_stroke_receipt")
            else:
                ink = data.get("strokes")
                medians = step["reference"]["medians"]
                if (
                    not isinstance(ink, list)
                    or len(ink) != len(medians)
                    or any(
                        not valid_points(p) or not matches_stroke(p, m)
                        for p, m in zip(ink, medians)
                    )
                ):
                    raise learning_error(422, "course_invalid_stroke_geometry")
        return correct, feedback, checked_draft(run), outcome
    if step["kind"] != "check" or len(answer) != 5:
        raise learning_error(422, "course_invalid_set_answer")
    q = step["questions"][0]
    if answer[2] != q["id"]:
        raise learning_error(422, "course_invalid_set_question")
    if (q.get("audio") or q.get("listeningRequired")) and not {"audio-heard", "transcript"} & set(
        supports
    ):
        raise learning_error(409, "course_listen_first")
    mode, value = answer[3:]
    if q["kind"] == "production":
        # These are explicit learner reports. Neither recorded audio nor acoustic
        # features leave the device, and no correctness/mastery is inferred.
        if q["response"] == "written":
            if mode != "hanzi" or not q.get("minLength", 1) <= len(value.strip()) <= 4000:
                raise learning_error(422, "course_invalid_production_response")
        elif (
            mode not in {"speech", "unrecorded"}
            or value
            or not (
                "recorded-locally" in supports
                if mode == "speech"
                else "practiced-aloud" in supports
            )
        ):
            raise learning_error(422, "course_invalid_production_response")
        if any(
            sum(f"review:{r['id']}:{v}" in supports for v in ("met", "retry")) != 1
            for r in q["rubric"]
        ):
            raise learning_error(422, "course_incomplete_practice_review")
        return (
            None,
            "Practice and self-review saved. This response has not been independently assessed.",
            checked_draft(run),
            "practiced",
        )
    if mode not in {"hanzi", "pinyin", "choice"} or len(value) > 240:
        raise learning_error(422, "course_invalid_set_answer")
    if q["kind"] == "choice":
        selected = next((c for c in q["choices"] if c["id"] == value), None)
        if mode != "choice" or selected is None:
            raise learning_error(422, "course_invalid_set_choice")
        correct = selected["correct"]
        return (
            correct,
            selected["feedback"],
            checked_draft(run),
            "correct" if correct else "incorrect",
        )
    if mode not in {"hanzi", "pinyin"}:
        raise learning_error(422, "course_invalid_set_mode")
    rule = q.get("rule", "word")
    correct = any(
        a.get("mode", "hanzi") == mode
        and normal(a["value"], mode, rule) == normal(value, mode, rule)
        for a in q["accepted"]
    )
    error = next(
        (
            e
            for e in q.get("errors", [])
            if normal(e["value"], mode, rule) == normal(value, mode, rule)
        ),
        None,
    )
    return (
        (True, "That matches the word.", checked_draft(run), "correct")
        if correct
        else (False, error["feedback"], checked_draft(run), "incorrect")
        if error
        else (
            None,
            "This answer is outside this example’s checker.",
            checked_draft(run),
            "unlisted",
        )
    )


def observed_characters(item, run):
    if status(item, run) != "completed":
        return []
    result = set()
    for step in definition_for_run(item, run["id"])["steps"]:
        if step["kind"] == "stroke":
            result.update(step["character"])
        for q in step.get("questions", []):
            for example in [q.get("display", {}), q.get("model", {})]:
                result.update(example.get("text", ""))
            for choice in q.get("choices", []):
                result.update(choice.get("hanzi", ""))
    return sorted(result)
