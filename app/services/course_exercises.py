"""Server-graded course practice and additive, account-bound guest import."""

import time
from collections import Counter

from sqlalchemy import select

from app.models.course_exercise import CourseExerciseRun
from app.models.learning import LearningEnrollment, LearningStage, WordBlock
from app.services import (
    course_assessments,
    course_eligibility,
    course_guest_receipts,
    course_sets,
    course_story,
    learning,
)
from app.services import course_content as content


def now():
    return time.time_ns() // 1_000_000


def status(run):
    item = content.catalog()[1].get(run["instanceId"])
    if item and is_current(run):
        item = content.exercise(run["instanceId"])
    if item and is_current(run) and course_sets.is_set(item):
        return course_sets.status(item, run)
    if item and is_current(run) and course_story.is_story(item):
        return course_story.status(item, run)
    if item and course_sets.is_set(item):
        return "draft"  # Unverified/stale set history has no assessed completion.
    attempts = run["attempts"]
    if any(item["correct"] for item in attempts):
        return "passed"
    return "failed" if len(attempts) == 2 else "retry" if attempts else "draft"


def is_current(run):
    item = content.catalog()[1].get(run["instanceId"])
    return (
        item is not None
        and course_sets.UNASSESSED not in run["supports"]
        and content.matches_content(item, run["revision"], run["fingerprint"])
    )


def canonical_run(run):
    """Use current media on the wire while retaining stored historical identity."""
    if is_current(run):
        return {**run, "fingerprint": content.exercise(run["instanceId"])["fingerprint"]}
    return run


def public_run(run, completed=()):
    current = is_current(run)
    result = {
        **canonical_run(run),
        "status": status(run),
        "archived": not current,
        "completed": current and run["instanceId"] in completed,
    }
    item = content.catalog()[1].get(run["instanceId"])
    if current and item and course_sets.is_set(item):
        item = content.exercise(run["instanceId"])
        result["setProgress"] = course_sets.progress(item, run)
        result["observedCharacters"] = course_sets.observed_characters(item, run)
        result["attempts"] = [
            course_sets.public_attempt(result, attempt) for attempt in run["attempts"]
        ]
    return result


def completed_prefix(passed):
    completed = set()
    for item in content.catalog()[0]["exercises"]:
        if item["id"] in passed:
            completed.add(item["id"])
    return completed


def support_union(*groups):
    # Criterion reports belong to their signed question attempt/draft, not the
    # run-wide help summary. A long review may contain many separate rubrics.
    result = sorted(
        {value for group in groups for value in group if not value.startswith("review:")}
    )
    if len(result) > 20:
        raise learning.learning_error(422, "course_invalid_support")
    return result


def record_value(row):
    return {
        "id": row.id,
        "instanceId": row.instance_id,
        "revision": row.revision,
        "fingerprint": row.fingerprint,
        "prepared": row.prepared,
        "draft": row.draft,
        "supports": row.supports,
        "attempts": row.attempts,
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
    }


def store_value(row, run):
    row.prepared = run["prepared"]
    row.draft = run["draft"]
    row.supports = run["supports"]
    row.attempts = run["attempts"]
    row.passed = status(run) in {"passed", "completed"}
    row.updated_at = run["updatedAt"]


def new_record(enrollment_id, run):
    row = CourseExerciseRun(
        enrollment_id=enrollment_id,
        id=run["id"],
        instance_id=run["instanceId"],
        revision=run["revision"],
        fingerprint=run["fingerprint"],
        created_at=run["createdAt"],
    )
    store_value(row, run)
    return row


def require_available(instance_id, completed, eligible=()):
    if instance_id in completed or content.is_optional(content.exercise(instance_id)):
        return  # Repeating completed practice never invalidates that completion.
    for item in content.catalog()[0]["exercises"]:
        if item["id"] == instance_id:
            return
        if not content.is_optional(item) and item["id"] in eligible and item["id"] not in completed:
            raise learning.learning_error(409, "course_exercise_locked")
    raise learning.learning_error(404, "course_exercise_not_found")


def required_before(block):
    if block.sequence is None:
        return set()
    catalog = content.catalog()[0]
    placements = catalog.get("learner_policy", {}).get("trail_placements", {})
    return {
        item["id"]
        for item in catalog["exercises"]
        if not content.is_optional(item)
        and placements.get(item["id"], {}).get("afterSequence", float("inf")) < block.sequence
    }


async def require_notebook_step(
    db, block, *, enrollment=None, notebook=None, runs=(), certified_completed=()
):
    """Only available required content gates new visits; existing visits stay valid."""
    track_id = enrollment.track_id if enrollment is not None else notebook.trackId
    if track_id != content.TRACK_ID:
        return
    required = required_before(block)
    if not required:
        return
    availability = await course_eligibility.assess(db, enrollment=enrollment, notebook=notebook)
    completed = (
        await completed_for(db, enrollment)
        if enrollment is not None
        else guest_completed(runs) | set(certified_completed)
    )
    if required.intersection(availability.reached).difference(completed):
        raise learning.learning_error(409, "course_exercise_locked")


def save_value(run_id, payload, run=None):
    item = content.exercise(payload.instanceId)
    content.current_content(item, payload.revision, payload.fingerprint)
    if run is None:
        stamp = now()
        run = {
            "id": str(run_id),
            "instanceId": item["id"],
            "revision": item["revision"],
            "fingerprint": item["fingerprint"],
            "prepared": False,
            "draft": [],
            "supports": [],
            "attempts": [],
            "createdAt": stamp,
            "updatedAt": stamp,
        }
    if (run["instanceId"], run["revision"]) != (
        item["id"],
        payload.revision,
    ) or not content.matches_content(item, run["revision"], run["fingerprint"]):
        raise learning.learning_error(409, "course_run_conflict")
    if course_sets.UNASSESSED in run["supports"]:
        raise learning.learning_error(409, "course_run_archived")
    run = canonical_run(run)
    if course_sets.is_set(item):
        course_sets.validate_draft(item, payload.draft, run)
    elif course_story.is_story(item):
        course_story.validate_draft(item, payload.draft, run)
    else:
        content.check_tiles(item, payload.draft)
    prepared = run["prepared"] or payload.prepared
    # `prepared` remains the wire-format readiness flag. New clients open the
    # exercise directly; retain explicit historical support without inventing it.
    supports = support_union(run["supports"], payload.supports)
    changed = (prepared, payload.draft, supports) != (
        run["prepared"],
        run["draft"],
        run["supports"],
    )
    if status(run) in {"passed", "failed", "completed"} and changed:
        raise learning.learning_error(409, "course_run_finished")
    return {
        **run,
        "prepared": prepared,
        "draft": list(payload.draft),
        "supports": supports,
        "updatedAt": max(now(), run["updatedAt"] + 1) if changed else run["updatedAt"],
    }


def check_value(run, payload):
    if course_sets.UNASSESSED in run["supports"]:
        raise learning.learning_error(409, "course_run_archived")
    item = content.exercise(run["instanceId"])
    content.current_content(item, run["revision"], run["fingerprint"])
    content.current_content(item, payload.revision, payload.fingerprint)
    run = canonical_run(run)
    submitted_supports = sorted(set(payload.supports))
    previous = next(
        (attempt for attempt in run["attempts"] if attempt["id"] == str(payload.attemptId)), None
    )
    if previous:
        if (
            not course_sets.same_submission(run, previous, payload)
            if course_sets.is_set(item)
            else previous["answer"] != payload.answer
        ) or previous["supports"] != submitted_supports:
            raise learning.learning_error(409, "course_attempt_conflict")
        return run
    if status(run) in {"passed", "failed", "completed"}:
        raise learning.learning_error(409, "course_run_finished")
    if not run["prepared"]:
        raise learning.learning_error(409, "course_preparation_required")
    outcome = None
    if course_sets.is_set(item):
        correct, feedback, draft, outcome = course_sets.grade(
            item,
            payload.answer,
            run,
            attempt_id=str(payload.attemptId),
            supports=submitted_supports,
            proof=payload.proof,
        )
    elif course_story.is_story(item):
        correct, feedback, draft = course_story.grade(item, payload.answer, run)
    else:
        correct, feedback = content.grade(item, payload.answer)
        draft = list(payload.answer)
    attempt = {
        "id": str(payload.attemptId),
        "answer": list(payload.answer),
        "supports": submitted_supports,
        "correct": correct,
        "feedback": feedback,
        **({"outcome": outcome} if outcome else {}),
        **({"proof": payload.proof} if payload.proof else {}),
    }
    return {
        **run,
        "draft": draft,
        "attempts": [*run["attempts"], attempt],
        "supports": support_union(
            run["supports"],
            submitted_supports,
            ["hint", "feedback"]
            if outcome in {"incorrect", "unlisted"} or outcome is None and correct is False
            else [],
        ),
        "updatedAt": max(now(), run["updatedAt"] + 1),
    }


async def enrollment_for(db, user_id, *, lock=False):
    if lock:
        await learning.ensure_enrollment(db, user_id, content.TRACK_ID)
    query = select(LearningEnrollment).where(
        LearningEnrollment.user_id == user_id, LearningEnrollment.track_id == content.TRACK_ID
    )
    return await db.scalar(query.with_for_update() if lock else query)


async def completed_for(db, enrollment):
    if enrollment is None:
        return set()
    rows = (
        await db.execute(
            select(
                CourseExerciseRun.instance_id,
                CourseExerciseRun.revision,
                CourseExerciseRun.fingerprint,
                CourseExerciseRun.supports,
            ).where(
                CourseExerciseRun.enrollment_id == enrollment.id, CourseExerciseRun.passed.is_(True)
            )
        )
    ).all()
    items = content.catalog()[1]
    return completed_prefix(
        {
            instance
            for instance, revision, fingerprint, supports in rows
            if course_sets.UNASSESSED not in supports
            and instance in items
            and content.matches_content(items[instance], revision, fingerprint)
        }
    )


def state_value(completed, runs, eligible=(), grammar_reviews=None, assessments=None):
    data = content.catalog()[0]
    return {
        "courseId": data["course_id"],
        "revision": data["revision"],
        "completed": [item["id"] for item in data["exercises"] if item["id"] in completed],
        "eligible": [item["id"] for item in data["exercises"] if item["id"] in eligible],
        "grammarReviews": grammar_reviews or {},
        "assessments": assessments or {},
        "runs": [public_run(run, completed) for run in runs],
    }


async def state(db, user_id):
    enrollment = await enrollment_for(db, user_id)
    completed = await completed_for(db, enrollment)
    rows = (
        []
        if enrollment is None
        else list(
            await db.scalars(
                select(CourseExerciseRun)
                .where(
                    CourseExerciseRun.enrollment_id == enrollment.id,
                    CourseExerciseRun.fingerprint.in_(
                        [
                            fingerprint
                            for item in content.catalog()[0]["exercises"]
                            for fingerprint in content.accepted_fingerprints(item)
                        ]
                    ),
                )
                .distinct(CourseExerciseRun.instance_id)
                .order_by(
                    CourseExerciseRun.instance_id,
                    CourseExerciseRun.updated_at.desc(),
                    CourseExerciseRun.id,
                )
            )
        )
    )
    availability = await course_eligibility.assess(db, enrollment=enrollment)
    return state_value(
        completed,
        [record_value(row) for row in rows],
        availability.eligible,
        availability.grammar_reviews,
        await course_assessments.account_summary(db, enrollment),
    )


async def detail(db, user_id, instance_id, *, run_id=None):
    item = content.exercise(instance_id)
    enrollment = await enrollment_for(db, user_id)
    availability = await course_eligibility.assess(db, enrollment=enrollment)
    course_eligibility.require_eligible(instance_id, availability.eligible)
    completed = await completed_for(db, enrollment)
    require_available(instance_id, completed, availability.reached)
    query = select(CourseExerciseRun)
    if run_id is not None:
        query = query.where(CourseExerciseRun.id == str(run_id))
    row = (
        None
        if enrollment is None
        else await db.scalar(
            query.where(
                CourseExerciseRun.enrollment_id == enrollment.id,
                CourseExerciseRun.instance_id == instance_id,
                CourseExerciseRun.fingerprint.in_(content.accepted_fingerprints(item)),
            )
            .order_by(CourseExerciseRun.updated_at.desc(), CourseExerciseRun.id)
            .limit(1)
        )
    )
    if run_id is not None and row is None:
        raise learning.learning_error(404, "course_run_not_found")
    wire = (
        {**item, "definition": course_sets.definition_for_run(item, str(row.id))}
        if row and course_sets.is_set(item)
        else item
    )
    return {"exercise": wire, "run": public_run(record_value(row), completed) if row else None}


async def save(db, user_id, run_id, payload):
    enrollment = await enrollment_for(db, user_id, lock=True)
    content.exercise(payload.instanceId)
    availability = await course_eligibility.assess(db, enrollment=enrollment)
    course_eligibility.require_eligible(payload.instanceId, availability.eligible)
    completed = await completed_for(db, enrollment)
    require_available(payload.instanceId, completed, availability.reached)
    row = await db.get(CourseExerciseRun, (enrollment.id, str(run_id)))
    run = save_value(run_id, payload, record_value(row) if row else None)
    if row:
        store_value(row, run)
    else:
        db.add(new_record(enrollment.id, run))
    await db.commit()
    return public_run(run, completed)


async def check(db, user_id, run_id, payload):
    enrollment = await enrollment_for(db, user_id, lock=True)
    row = await db.get(CourseExerciseRun, (enrollment.id, str(run_id)))
    if row is None:
        raise learning.learning_error(404, "course_run_not_found")
    availability = await course_eligibility.assess(db, enrollment=enrollment)
    course_eligibility.require_eligible(row.instance_id, availability.eligible)
    completed = await completed_for(db, enrollment)
    require_available(row.instance_id, completed, availability.reached)
    run = check_value(record_value(row), payload)
    store_value(row, run)
    await course_assessments.store(db, enrollment.id, [run])
    await db.commit()
    return public_run(run, await completed_for(db, enrollment))


def guest_completed(runs):
    return completed_prefix(
        {
            run["instanceId"]
            for run in runs
            if is_current(run) and status(run) in {"passed", "completed"}
        }
    )


def validate_guest(progress, *, preview=False):
    from app.schemas.course_exercise import CourseCheck

    if progress.developmentPreview and not preview:
        raise learning.learning_error(422, "development_preview_progress_not_importable")
    if len({str(run.id) for run in progress.runs}) != len(progress.runs):
        raise learning.learning_error(422, "course_invalid_guest_progress")
    if any(count > 2 for count in Counter(run.instanceId for run in progress.runs).values()):
        raise learning.learning_error(422, "course_invalid_guest_progress")
    positions = {item["id"]: index for index, item in enumerate(content.catalog()[0]["exercises"])}
    validated = {}
    for submitted in sorted(progress.runs, key=lambda run: positions.get(run.instanceId, -1)):
        if len({str(attempt.id) for attempt in submitted.attempts}) != len(submitted.attempts):
            raise learning.learning_error(422, "course_invalid_guest_progress")
        item = content.exercise(submitted.instanceId)
        unverified_set = course_sets.is_set(item) and (
            course_sets.UNASSESSED in submitted.supports
            or any(
                not course_sets.authentic_history(
                    submitted.model_dump(mode="json"), attempt.model_dump(mode="json")
                )
                for attempt in submitted.attempts
            )
        )
        if unverified_set or not content.matches_content(
            item, submitted.revision, submitted.fingerprint
        ):
            # The old definition is unavailable for honest regrading. Preserve
            # bounded answers as history, never client-claimed grades or unlocks.
            validated[str(submitted.id)] = {
                "id": str(submitted.id),
                "instanceId": submitted.instanceId,
                "revision": submitted.revision,
                "fingerprint": submitted.fingerprint,
                "prepared": submitted.prepared,
                "draft": list(submitted.draft),
                "supports": sorted(set(submitted.supports[:19]) | {course_sets.UNASSESSED})
                if unverified_set
                else support_union(submitted.supports),
                "attempts": [
                    {
                        "id": str(attempt.id),
                        "answer": list(attempt.answer),
                        "supports": sorted(set(attempt.supports)),
                        "correct": None,
                        "feedback": None,
                    }
                    for attempt in submitted.attempts
                ],
                "createdAt": submitted.createdAt,
                "updatedAt": submitted.updatedAt,
            }
            continue
        previous_fingerprint = submitted.fingerprint
        submitted = submitted.model_copy(update={"fingerprint": item["fingerprint"]})
        # Regrade stored evidence independently of present prerequisite gaps.
        # Completion follows learner policy: optional tasks remain independent,
        # including after a content update or when earlier practice is skipped.
        restored = (
            submitted.model_copy(update={"draft": []})
            if course_story.is_story(item) or course_sets.is_set(item)
            else submitted
        )
        run = save_value(submitted.id, restored)
        for attempt in submitted.attempts:
            proof = attempt.proof
            if course_sets.is_set(item) and previous_fingerprint != item["fingerprint"]:
                # Every original event was authenticated above, against its
                # retained identity. Only an explicit media compatibility alias
                # reaches this branch; re-sign before replaying ink-free events.
                proof = course_sets.receipt(run, attempt.model_dump(mode="json"))
            run = check_value(
                run,
                CourseCheck(
                    attemptId=attempt.id,
                    revision=submitted.revision,
                    fingerprint=submitted.fingerprint,
                    answer=attempt.answer,
                    supports=attempt.supports,
                    proof=proof,
                ),
            )
        # Validate the persisted draft independently of any forged outcome fields.
        if status(run) in {"passed", "failed", "completed"} and run["draft"] != submitted.draft:
            raise learning.learning_error(422, "course_invalid_guest_progress")
        if course_sets.is_set(item):
            course_sets.validate_draft(item, submitted.draft, run)
        elif course_story.is_story(item):
            course_story.validate_draft(item, submitted.draft, run)
        run["draft"] = list(submitted.draft)
        run["supports"] = support_union(run["supports"], submitted.supports)
        # Local timestamps are display metadata, never ownership or unlock evidence.
        run["createdAt"] = submitted.createdAt
        run["updatedAt"] = submitted.updatedAt
        validated[str(submitted.id)] = run
    return [validated[str(run.id)] for run in progress.runs]


def compact_guest(runs):
    latest = {run["instanceId"]: run["id"] for run in runs}
    latest.update({run["instanceId"]: run["id"] for run in runs if is_current(run)})
    passed = {
        run["instanceId"]: run["id"]
        for run in runs
        if is_current(run) and status(run) in {"passed", "completed"}
    }
    archived = {run["instanceId"]: run["id"] for run in runs if not is_current(run)}
    previous = {
        run["instanceId"]: run["id"] for run in runs if run["id"] != latest.get(run["instanceId"])
    }
    historical = {**previous, **archived, **passed}
    keep = set(latest.values()) | set(historical.values())
    return [run for run in runs if run["id"] in keep]


async def guest_request(db, payload, *, preview=False):
    if payload.progress.developmentPreview and not preview:
        raise learning.learning_error(422, "development_preview_progress_not_importable")
    if preview:
        from app.services import guest_learning

        if payload.notebook is None:
            raise learning.learning_error(422, "course_payload_required")
        await guest_learning.validate_progress(db, payload.notebook, preview=True)
        if payload.notebook.trackId != content.TRACK_ID:
            raise learning.learning_error(422, "learning_invalid_guest_progress")
        if payload.operation == "advance":
            block = await db.scalar(
                select(WordBlock)
                .join(LearningStage)
                .where(
                    LearningStage.track_id == content.TRACK_ID,
                    WordBlock.id == payload.blockId,
                    WordBlock.kind == "word",
                )
            )
            if block is None:
                raise learning.learning_error(404, "learning_block_not_found")
            return {
                "allowed": True,
                "progress": {
                    **payload.progress.model_dump(mode="json"),
                    "developmentPreview": True,
                },
            }
        return guest(payload, set(content.catalog()[1]), {}, preview=True)
    if payload.operation == "advance":
        from app.services import guest_learning

        if payload.notebook is None or not payload.blockId:
            raise learning.learning_error(422, "course_payload_required")
        notebook = await guest_learning.validate_progress(db, payload.notebook)
        block = await learning.next_word_for(db, notebook)
        if payload.blockId in notebook.completed:
            return {"allowed": True, "progress": payload.progress.model_dump(mode="json")}
        if block is None or block.id != payload.blockId:
            raise learning.learning_error(409, "learning_block_locked")
        if payload.notebook.trackId != content.TRACK_ID or not required_before(block):
            return {"allowed": True, "progress": payload.progress.model_dump(mode="json")}
        # Unavailable/optional history cannot block notebook progress. Regrade
        # only the required occurrences that could govern this forward visit.
        required = required_before(block)
        runs = validate_guest(
            payload.progress.model_copy(
                update={
                    "runs": [run for run in payload.progress.runs if run.instanceId in required]
                }
            )
        )
        receipts = course_guest_receipts.verified(payload.progress.receipts)
        await require_notebook_step(
            db,
            block,
            notebook=payload.notebook,
            runs=runs,
            certified_completed=course_guest_receipts.completed(receipts.values()),
        )
        return {"allowed": True, "progress": payload.progress.model_dump(mode="json")}
    availability = await course_eligibility.assess(db, notebook=payload.notebook)
    return guest(payload, availability.eligible, availability.grammar_reviews)


def guest(payload, eligible=(), grammar_reviews=None, *, preview=False):
    receipts = course_guest_receipts.verified(payload.progress.receipts)
    runs = validate_guest(payload.progress, preview=preview)
    coverage_runs = runs
    coverage = course_assessments.guest_receipts(runs, payload.progress.coverage)
    completed = guest_completed(runs) | course_guest_receipts.completed(receipts.values())
    response = {}
    if payload.operation == "state":
        response = state_value(
            completed,
            [],
            eligible,
            grammar_reviews,
            course_assessments.project(course_assessments.verified_receipts(coverage)),
        )
    elif payload.operation == "exercise":
        item = content.exercise(payload.instanceId)
        course_eligibility.require_eligible(item["id"], eligible)
        if not preview:
            require_available(item["id"], completed, set(eligible) | (grammar_reviews or {}).keys())
        latest = next(
            (
                run
                for run in reversed(runs)
                if run["instanceId"] == item["id"]
                and is_current(run)
                and (payload.runId is None or run["id"] == str(payload.runId))
            ),
            None,
        )
        if payload.runId is not None and latest is None:
            raise learning.learning_error(404, "course_run_not_found")
        wire = (
            {**item, "definition": course_sets.definition_for_run(item, latest["id"])}
            if latest and course_sets.is_set(item)
            else item
        )
        response = {"exercise": wire, "run": public_run(latest, completed) if latest else None}
    elif payload.runId is None:
        raise learning.learning_error(422, "course_run_required")
    else:
        existing = next((run for run in runs if run["id"] == str(payload.runId)), None)
        if payload.operation == "save" and payload.save:
            content.exercise(payload.save.instanceId)
            course_eligibility.require_eligible(payload.save.instanceId, eligible)
            if not preview:
                require_available(
                    payload.save.instanceId,
                    completed,
                    set(eligible) | (grammar_reviews or {}).keys(),
                )
            changed = save_value(payload.runId, payload.save, existing)
        elif payload.operation == "check" and payload.check:
            if existing is None:
                raise learning.learning_error(404, "course_run_not_found")
            course_eligibility.require_eligible(existing["instanceId"], eligible)
            if not preview:
                require_available(
                    existing["instanceId"],
                    completed,
                    set(eligible) | (grammar_reviews or {}).keys(),
                )
            changed = check_value(existing, payload.check)
        else:
            raise learning.learning_error(422, "course_payload_required")
        runs = [run for run in runs if run["id"] != changed["id"]] + [changed]
        coverage_runs = runs
        coverage = course_assessments.guest_receipts(runs, coverage)
        runs = compact_guest(runs)
        completed |= guest_completed(runs)
        response = {"run": public_run(changed, completed)}
    receipt_values = course_guest_receipts.merge(receipts, runs, completed)
    if payload.operation == "state":
        response["runs"] = course_guest_receipts.public_latest(receipt_values)
    return {
        **response,
        "coverageContributions": course_assessments.guest_contributions(coverage_runs),
        "progress": {
            "runs": [public_run(run, completed) for run in runs],
            "coverage": coverage,
            "receipts": receipt_values,
            **({"developmentPreview": True} if preview else {}),
        },
    }


async def import_guest(db, user_id, payload):
    if payload.accountId != user_id:
        raise learning.learning_error(409, "learning_account_changed")
    enrollment = await enrollment_for(db, user_id, lock=True)
    runs = validate_guest(payload.progress)
    # Validate every conflict before staging writes; the transaction remains atomic.
    merges = []
    imported_at = now()
    for index, run in enumerate(runs):
        row = await db.get(CourseExerciseRun, (enrollment.id, run["id"]))
        if row:
            existing = record_value(row)
            if any(
                canonical_run(existing)[key] != run[key]
                for key in ["instanceId", "revision", "fingerprint"]
            ):
                raise learning.learning_error(409, "course_run_conflict")
            if not is_current(run):
                continue  # An unassessed archive never replaces account history.
            length = min(len(existing["attempts"]), len(run["attempts"]))

            def evidence(events):
                # Signatures are reissued after an explicitly compatible media
                # update; compare the authenticated answers/outcomes themselves.
                return [
                    {key: value for key, value in attempt.items() if key != "proof"}
                    for attempt in events
                ]

            if evidence(existing["attempts"][:length]) != evidence(run["attempts"][:length]):
                raise learning.learning_error(409, "course_attempt_conflict")
            if len(existing["attempts"]) > len(run["attempts"]) or status(existing) in {
                "passed",
                "failed",
                "completed",
            }:
                continue
            run = {
                **run,
                "prepared": existing["prepared"] or run["prepared"],
                "supports": support_union(existing["supports"], run["supports"]),
            }
            if all(
                existing[key] == run[key] for key in ["prepared", "supports", "draft", "attempts"]
            ):
                continue
        # Guest arrays retain last-pass then latest-run order. Preserve that order
        # even when several rows are imported within the same millisecond.
        stamp = max(imported_at + index, row.updated_at + 1 if row else 0)
        run = {**run, "createdAt": row.created_at if row else stamp, "updatedAt": stamp}
        merges.append((row, run))
    for row, run in merges:
        if row:
            store_value(row, run)
        else:
            db.add(new_record(enrollment.id, run))
    await course_assessments.store(db, enrollment.id, runs, payload.progress.coverage)
    await db.commit()
    return {"ok": True, **(await state(db, user_id) if payload.finalize else {})}
