"""Content-only cumulative forms and honest revision-bound coverage summaries."""

import copy
import hashlib
import hmac
import json
import math
import re
from collections import defaultdict
from functools import lru_cache


def form_index(run_id):
    return int(str(run_id).replace("-", "")[:8], 16)


@lru_cache(maxsize=48)
def bank_body(sha):
    from app.services.course_content import CONTENT

    if not re.fullmatch("[a-f0-9]{64}", sha):
        raise ValueError("Invalid cumulative bank reference")
    body = json.loads((CONTENT.parent / "practice-bank" / (sha + ".json")).read_text())
    actual = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if actual != sha:
        raise ValueError("Changed cumulative bank content")
    return body


@lru_cache(maxsize=24)
def materialize(instance_id, fingerprint, index):
    from app.services import course_content

    item = course_content.exercise(instance_id)
    if item["fingerprint"] != fingerprint:
        raise ValueError("Stale cumulative form")
    definition = item["definition"]
    bank = definition["setActivity"]["formBank"]
    index %= form_count(definition)
    groups = defaultdict(list)
    for row in bank["entries"]:
        groups[row["domain"]].append(row)
    groups["writing"] = bank["writing"]
    steps = []
    audio = {}
    coverage = []
    targets = []
    fixed = [step for step in definition["steps"] if step["id"] in bank["fixedPartIds"]]
    for domain in ["characters", "vocabulary", "grammar", "skills", "writing"]:
        entries = groups[domain]
        quota = min(bank["quotas"][domain], len(entries))
        for offset in range(quota):
            row = entries[(index * quota + offset) % len(entries)]
            body = bank_body(row["sha256"])
            if len(steps) + len(body["steps"]) + len(fixed) > 128:
                raise ValueError("Cumulative form exceeds its independently resumable part budget")
            steps.extend(copy.deepcopy(body["steps"]))
            audio.update(copy.deepcopy(body["audio"]))
            coverage.extend(copy.deepcopy(body["coverage"]))
            targets.append(row["targetId"])
    steps.extend(copy.deepcopy(fixed))
    coverage.extend(
        copy.deepcopy(row)
        for row in item.get("coverage", [])
        if row["part_id"] in bank["fixedPartIds"]
    )
    audio.update(copy.deepcopy(definition.get("audio", {})))
    result = {
        **definition,
        "steps": steps,
        "audio": audio,
        "setActivity": {
            key: value for key, value in definition["setActivity"].items() if key != "formBank"
        },
    }
    result["setActivity"].update(
        partIds=[step["id"] for step in steps],
        selectedTargets=targets,
        formIndex=index,
        coverage=coverage,
    )
    result["assessment"] = {
        **definition["assessment"],
        "form": {
            **definition["assessment"]["form"],
            "id": instance_id + "-form-" + str(index + 1),
            "index": index + 1,
            "selected_targets": targets,
            "maximum_parts": 128,
        },
    }
    return result


def form_count(definition):
    bank = definition["setActivity"]["formBank"]
    groups = defaultdict(int)
    for entry in bank["entries"]:
        groups[entry["domain"]] += 1
    groups["writing"] = len(bank["writing"])
    return max(
        (math.ceil(count / bank["quotas"][domain]) for domain, count in groups.items()), default=1
    )


def summaries(runs):
    from app.services import course_content, course_exercises, course_sets

    result = {}
    for run in runs:
        if not course_exercises.is_current(run):
            continue
        if course_content.catalog()[1][run["instanceId"]]["definition"]["id"] != "ACT-MILESTONE":
            continue
        item = course_content.exercise(run["instanceId"])
        if not item["definition"].get("setActivity", {}).get("formBank"):
            continue
        value = result.setdefault(
            item["id"],
            {**{field: set() for field in FIELDS}, "forms": form_count(item["definition"])},
        )
        definition = materialize(item["id"], item["fingerprint"], form_index(run["id"]))
        for row in definition["setActivity"]["coverage"]:
            attempts = [
                a
                for a in run["attempts"]
                if len(a["answer"]) >= 3
                and a["answer"][0] == course_sets.PROTOCOL
                and a["answer"][1] == row["part_id"]
            ]
            if not attempts:
                continue
            facet = row["target_id"] + ":" + row["facet_id"]
            if row["question_ids"]:
                answered = {
                    a["answer"][2] for a in attempts if a["answer"][2] in row["question_ids"]
                }
                if not set(row["question_ids"]) <= answered:
                    continue
                value["observedFacets"].add(facet)
                if all(
                    any(a["answer"][2] == qid and a.get("correct") is True for a in attempts)
                    for qid in row["question_ids"]
                ):
                    value["checkedFacets"].add(facet)
                    first = [
                        next(a for a in attempts if a["answer"][2] == qid)
                        for qid in row["question_ids"]
                    ]
                    field = (
                        "initialCheckedFacets"
                        if all(
                            a.get("correct") is True and not set(a["supports"]) - {"audio-heard"}
                            for a in first
                        )
                        else "supportedCheckedFacets"
                    )
                    value[field].add(facet)
                elif row["evidence"] == "self-reviewed" and all(
                    any(a["answer"][2] == qid and a.get("outcome") == "practiced" for a in attempts)
                    for qid in row["question_ids"]
                ):
                    value["selfReviewedFacets"].add(facet)
            else:
                for attempt in attempts:
                    if attempt["answer"][2] != "stroke":
                        continue
                    value["observedFacets"].add(facet)
                    if attempt.get("outcome") == "paper":
                        value["paperWritingFacets"].add(facet)
                    elif attempt.get("correct") is True:
                        evidence = json.loads(attempt["answer"][4])
                        field = (
                            "recalledWritingFacets"
                            if evidence.get("evidence") == "recall"
                            and not evidence.get("corrections")
                            else "guidedWritingFacets"
                            if evidence.get("evidence") == "guided"
                            else "supportedWritingFacets"
                        )
                        value[field].add(facet)
        if course_sets.status(item, run) == "completed":
            value["completedForms"].add(definition["setActivity"]["formIndex"])
    for value in result.values():
        next_index = next(
            (n for n in range(value["forms"]) if n not in value["completedForms"]), value["forms"]
        )
        value.update(nextFormIndex=next_index)
        for key in FIELDS:
            value[key] = sorted(value[key])
    return result


FIELDS = (
    "observedFacets",
    "checkedFacets",
    "initialCheckedFacets",
    "supportedCheckedFacets",
    "selfReviewedFacets",
    "guidedWritingFacets",
    "recalledWritingFacets",
    "supportedWritingFacets",
    "paperWritingFacets",
    "completedForms",
)


def merge_evidence(*records):
    return {
        field: sorted({item for record in records for item in record.get(field, [])})
        for field in FIELDS
    }


def coverage_proof(value):
    from app.services import course_sets
    from app.services.learning import learning_error

    key = course_sets.signing_key.get()
    if not key:
        raise learning_error(503, "course_signing_unavailable")
    body = [
        "coverage-v1",
        course_sets.receipt_scope.get(),
        value["instanceId"],
        value["fingerprint"],
        *[value[field] for field in FIELDS],
    ]
    if value.get("runId") is not None:
        body = ["coverage-run-v1", str(value["runId"]), *body[1:]]
    return hmac.new(
        key.encode(),
        json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode(),
        hashlib.sha256,
    ).hexdigest()


def verified_receipts(receipts):
    from app.services.learning import learning_error

    result = {}
    for submitted in receipts:
        value = submitted.model_dump(mode="json") if hasattr(submitted, "model_dump") else submitted
        if not hmac.compare_digest(coverage_proof(value), value.get("proof", "")):
            raise learning_error(422, "course_invalid_guest_progress")
        identity = value["instanceId"], value["fingerprint"]
        result[identity] = merge_evidence(result.get(identity, {}), value)
    return result


def guest_receipts(runs, receipts):
    from app.services import course_content

    values = verified_receipts(receipts)
    for instance_id, evidence in summaries(runs).items():
        identity = instance_id, course_content.exercise(instance_id)["fingerprint"]
        values[identity] = merge_evidence(values.get(identity, {}), evidence)
    result = []
    for (instance_id, fingerprint), evidence in sorted(values.items()):
        value = {"instanceId": instance_id, "fingerprint": fingerprint, **evidence}
        result.append({**value, "proof": coverage_proof(value)})
    return result


def guest_contributions(runs):
    """Independently signed inputs let the browser partition an in-flight union."""
    result = []
    for run in runs:
        for receipt in guest_receipts([run], []):
            value = {**receipt, "runId": run["id"]}
            result.append({**value, "proof": coverage_proof(value)})
    return result


def project(records):
    """Only current-content facets are coverage; older receipts remain history."""
    from app.services import course_content

    result = {}
    for (instance_id, fingerprint), value in records.items():
        item = course_content.catalog()[1].get(instance_id)
        if not item or fingerprint not in course_content.accepted_fingerprints(item):
            continue
        definition = course_content.exercise(instance_id)["definition"]
        if not definition.get("setActivity", {}).get("formBank"):
            continue
        count = form_count(definition)
        evidence = merge_evidence(result.get(instance_id, {}), value)
        result[instance_id] = {
            **evidence,
            "forms": count,
            "nextFormIndex": next(
                (index for index in range(count) if index not in evidence["completedForms"]), 0
            ),
        }
    return result


async def store(db, enrollment_id, runs=(), receipts=()):
    """Caller holds the enrollment lock; stored evidence only grows per revision."""
    from app.models.course_exercise import CourseAssessmentCoverage
    from app.services import course_content, course_exercises

    values = verified_receipts(receipts)
    for instance_id, evidence in summaries(runs).items():
        identity = instance_id, course_content.exercise(instance_id)["fingerprint"]
        values[identity] = merge_evidence(values.get(identity, {}), evidence)
    for (instance_id, fingerprint), evidence in values.items():
        row = await db.get(CourseAssessmentCoverage, (enrollment_id, instance_id, fingerprint))
        if row:
            row.evidence = merge_evidence(row.evidence, evidence)
            row.updated_at = course_exercises.now()
        else:
            db.add(
                CourseAssessmentCoverage(
                    enrollment_id=enrollment_id,
                    instance_id=instance_id,
                    fingerprint=fingerprint,
                    evidence=evidence,
                    updated_at=course_exercises.now(),
                )
            )


async def account_summary(db, enrollment):
    if enrollment is None:
        return {}
    from sqlalchemy import select

    from app.models.course_exercise import CourseAssessmentCoverage

    rows = await db.scalars(
        select(CourseAssessmentCoverage).where(
            CourseAssessmentCoverage.enrollment_id == enrollment.id
        )
    )
    return project({(row.instance_id, row.fingerprint): row.evidence for row in rows})
