# Approved course exercise progress

The authored course runs alongside the existing `mandarin-simplified-v1`
enrollment. Migration `0019_course_exercise_runs` adds account-owned run records;
it does not reorder or update notebooks, vocabulary exposures or word completions.
These exercise endpoints have no subscription requirement.

The frontend master plan exports the trusted
`app/data/learning/course-exercises.json` manifest. From the frontend repository,
run `python3 scripts/course-rollout.py export-backend` after content review and
both-voice audio qualification; append `--check` to verify a synchronized export
without writing. The manifest's ordered exercises include exact definitions,
preparation and SHA-256 fingerprints. Runtime never authors questions or fetches
the frontend repository. Content caches contain public definitions only.

Every required predecessor must have a passing run at its current fingerprint
before a new exercise can start. Repeating a completed exercise is permitted.
Preparation explicitly introduces the displayed word senses and grammar before
checking; it is support/exposure, not character-history completion or mastery.
Clients acknowledge preparation by saving `prepared: true`.

## Authenticated endpoints

All responses are `Cache-Control: no-store`; verified session and the existing
CSRF/origin rules apply. UUIDs are scoped to the authenticated enrollment. A user
cannot read or modify another account's run by supplying its ID.

- `GET /learning/course` returns `{courseId, revision, completed, runs}`. `runs`
  contains the latest current-fingerprint run per instance; `completed` preserves a prior pass even
  when the latest repeat fails.
- `GET /learning/course/exercises/{instanceId}` returns `{exercise, run}` with
  the current manifest entry and latest saved run, or `null` if none exists.
- `PUT /learning/course/runs/{runId}` creates or saves a run. Body:
  `{instanceId, revision, fingerprint, prepared, draft: [tileId], supports: []}`.
- `POST /learning/course/runs/{runId}/checks` checks the current response. Body:
  `{attemptId, revision, fingerprint, answer: [tileId], supports: []}`.

Save/check return the run directly:

```json
{
  "id": "client-generated UUID",
  "instanceId": "EX-CLOZE-WU01",
  "revision": 1,
  "fingerprint": "64 hexadecimal characters",
  "prepared": true,
  "draft": [],
  "supports": ["preparation"],
  "status": "draft",
  "attempts": [],
  "completed": false,
  "archived": false,
  "createdAt": 0,
  "updatedAt": 0
}
```

An attempt is `{id, answer, supports, correct, feedback}`. Status is `draft`,
`retry`, `passed` or `failed`; there are at most two checks per run. Empty answers
are valid failed attempts. Only authored complete solutions pass; physical tile
IDs must exist and cannot be reused. Identical word copies are interchangeable,
while different readings are distinct. Failure preserves the attempt and exposes
the authored repair hint. A terminal run is immutable; later practice uses a new
UUID. Repeating an identical attempt ID and payload is idempotent; a conflicting
payload returns `course_attempt_conflict` (409). Enrollment locking serializes
concurrent checks and imports.

`completed` describes the current exercise's saved completion, while `status`
and attempts describe this run. A failed repeat can therefore have
`completed: true`. Drafts survive reload. Old revision/fingerprint records remain
stored; further writes to stale content return `course_content_changed` (409).
A pass for an older fingerprint is not silently reassigned to changed content.
Current completion is the contiguous required passing prefix. If earlier content
changes, later unchanged passing evidence remains stored and becomes current
completion again when the revised prerequisite is passed.
Generic notebook-completion endpoints cannot complete these exercise IDs.

## Guest practice and import

`POST /learning/course/guest` is read-only with respect to the database. It takes
`{operation, progress: {runs}, ...}` and applies the same grading, preparation,
content and predecessor rules. Client-supplied correctness, status and completion
fields are recomputed, never trusted.

| Operation | Additional request fields | Response |
| --- | --- | --- |
| `state` | none | `{courseId, revision, completed, runs, progress}` |
| `exercise` | `instanceId` | `{exercise, run, progress}` |
| `save` | `runId`, `save` using the authenticated save body | `{run, progress}` |
| `check` | `runId`, `check` using the authenticated check body | `{run, progress}` |

Store the returned `progress`, not guessed pass flags. It retains the latest run
and last passing run per instance, at most 568 runs across the 284 exercises.
Before a new current pass, the second slot can retain the latest old-content
archive. Archives have `archived: true`, `completed: false`, and unassessed
`correct: null` / `feedback: null` attempts. They stay in `progress.runs`, but
never become the actionable `run` or grant unlocks. Stored current attempts are
regraded even when an earlier exercise changed; only the valid current passing
prefix grants completion. New save/check operations still enforce prerequisites.
Each has at most two attempts. Requests have bounded fields and a 4 MB body cap;
no media or microphone recordings belong in progress. Existing guest notebook
progress remains separate and compatible.

After verification, `POST /learning/course/import` accepts
`{accountId, progress: {runs}}` and returns `{ok: true, courseId, revision,
completed, runs}`. The account ID must equal the current verified account. Import
regrades the submitted answers, validates prerequisites and conflicts, then adds
the records in one transaction. Compatible older client snapshots never erase
newer account attempts or a prior pass. Conflicting histories fail atomically.
Repeated imports do not duplicate runs. Keep account-bound pending guest data
until successful import; retain it on network, account or conflict errors.
Stale guest archives import as unassessed history with `passed: false`; existing
matching account history is never overwritten by such an archive. Archives do
not block import and do not need a separate ledger or database column.

Validation uses public HTTP actions, including concurrent retries and migration
from the previous schema. Run `make ci-core` and `make release-schema` after
backend changes. Local validation does not deploy or migrate production.
