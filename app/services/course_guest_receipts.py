"""Signed compact state for other occurrences; full answers stay on the device."""

import hashlib
import hmac
import json

from app.services import course_sets, learning


def proof(receipt):
    key = course_sets.signing_key.get()
    if not key:
        raise learning.learning_error(503, "course_signing_unavailable")
    body = [
        "course-run-summary-v1",
        course_sets.receipt_scope.get(),
        receipt["run"],
        receipt["events"],
    ]
    return hmac.new(
        key.encode(),
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(),
        hashlib.sha256,
    ).hexdigest()


def verified(values):
    result = {}
    for submitted in values:
        receipt = (
            submitted.model_dump(mode="json") if hasattr(submitted, "model_dump") else submitted
        )
        if not hmac.compare_digest(proof(receipt), receipt["proof"]):
            raise learning.learning_error(422, "course_invalid_guest_progress")
        previous = result.get(receipt["run"]["id"])
        if previous is None or (receipt["events"], receipt["run"]["updatedAt"]) > (
            previous["events"],
            previous["run"]["updatedAt"],
        ):
            result[receipt["run"]["id"]] = receipt
    return result


def issue(run, completed):
    from app.schemas.course_exercise import GuestCourseRunSummary
    from app.services import course_exercises

    public = course_exercises.public_run(run, completed)
    public.pop("attempts")
    public.pop("draft")
    public["completed"] = not public["archived"] and public["status"] in {"passed", "completed"}
    header = GuestCourseRunSummary(**public).model_dump(mode="json")
    receipt = {"run": header, "events": len(run["attempts"])}
    return {**receipt, "proof": proof(receipt)}


def current(receipt):
    from app.services import course_content, course_exercises

    run = receipt["run"]
    if not course_exercises.is_current(run):
        return {**run, "archived": True, "completed": False}
    return {
        **run,
        "fingerprint": course_content.catalog()[1][run["instanceId"]]["fingerprint"],
        "archived": False,
    }


def completed(receipts):
    return {
        run["instanceId"] for receipt in receipts for run in [current(receipt)] if run["completed"]
    }


def merge(receipts, runs, completed_ids):
    values = dict(receipts)
    for run in runs:
        values[run["id"]] = issue(run, completed_ids)
    groups = {}
    for receipt in values.values():
        groups.setdefault(receipt["run"]["instanceId"], []).append(receipt)
    result = []
    for rows in groups.values():
        rows.sort(key=lambda row: (row["run"]["updatedAt"], row["events"], row["run"]["id"]))
        live = [row for row in rows if not current(row)["archived"]]
        latest = (live or rows)[-1]
        successful = next((row for row in reversed(live) if current(row)["completed"]), None)
        historical = (
            successful
            or next((row for row in reversed(rows) if current(row)["archived"]), None)
            or next(
                (row for row in reversed(rows) if row["run"]["id"] != latest["run"]["id"]), None
            )
        )
        if historical and historical["run"]["id"] != latest["run"]["id"]:
            result.append(historical)
        result.append(latest)
    return result


def public_latest(receipts):
    result = {}
    passed = completed(receipts)
    for receipt in sorted(receipts, key=lambda row: (row["run"]["updatedAt"], row["events"])):
        run = current(receipt)
        if not run["archived"]:
            result[run["instanceId"]] = {
                **run,
                "completed": run["instanceId"] in passed,
                "draft": [],
                "attempts": [],
            }
    return list(result.values())
