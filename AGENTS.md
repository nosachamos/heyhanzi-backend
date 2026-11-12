# Repository instructions

- Keep the established conventions: `app/`, `app/routers`, `app/models`, `app/services`,
  `app/database`, `.configs.json` / `APP_CONFIGS`, Alembic, and Make entry points.
- Use async database I/O in request paths. Routers stay thin. Services own
  transactions and explicitly commit; never retry arbitrary request bodies.
- API startup never creates databases, tables, or runs migrations. Compose's
  bootstrap service and Fly's release command own migration execution.
- Tests exercise public HTTP/CLI operations and observable results. Doubles belong
  only at external I/O boundaries; do not inspect source or mutate private members.
  Run `make ci-core` and, for database changes, `make release-schema`.
- Keep core CI bounded. Production deployment is manual from master, with every
  applicable release check mandatory. No skip/reset inputs or automatic deploys.
- Production data is permanent. No production reset or destructive rollback.
  Changes must support compatible migrations and forward repair.
- Prefer expanding the vocabulary taught to support richer, natural example
  sentences. When an example needs a useful compound made from available
  characters, add it to the vocabulary dataset and introduce it on an appropriate
  earlier card or list it on the current card before relying on it. Keep the
  character-and-displayed-vocabulary guard as a safety check; investigate missing
  vocabulary coverage before shortening examples or choosing generic practice
  sentences. Knowing constituent characters alone never proves a compound was
  introduced; preserve explicit per-learner exposure tracking.
- Secrets live in ignored local files or encrypted Parameter Store. Never print,
  commit, bake into an image, or copy another service's product/database credentials here.
- Preserve the measured 768 MiB / one shared CPU / one API worker Fly budget
  until new measurements require a change. The former 256 MiB initial allocation
  cannot load the full authored course: isolated parsing peaked at 531 MiB RSS.
  The current real-image course/auth rehearsal measured 564 MiB peak RSS and
  358 MiB steady RSS, with no cgroup OOM events under 768 MiB. The prepared Fly
  configuration is a reviewable release change, not a production deployment.
  `make release-container` must exercise the full actual course catalog,
  authenticated and guest course state/detail, repeated migrations and auth
  under the cap, retaining PID/cgroup peak evidence. Health-only checks do not
  establish course-runtime readiness.
- The initial footprint is not a capacity guarantee. Add domain models, workers,
  indexes, caching and scaling based on actual requirements and load measurements.

- Audio generation must follow `docs/audio-production.md`: both adult voices,
  natural connected Mandarin, automated acceptance and bounded retries, no extreme forced
  slow instruction/time stretching, no lossless retention, no runtime synthesis.
  Profile/quality changes require the representative pilot before bulk generation.
  Never publish capped/incomplete or rejected takes, or weaken checks to complete
  a batch. Human listening happens after automated qualification, not as a gate.
  The owner's explicit 2026-10-15 approval of two exact MP3s is recorded in
  `app/data/sentence-audio/manual-approvals.json`; retain those SHA-bound exceptions
  and their failed automated pronunciation results as documented in the audio policy.
  The owner's 2026-10-22 course-rollout instruction additionally permits the
  measured best usable take after ten failed retained attempts. Use the explicit
  owner-selection path in the audio policy: exact-byte provenance and failed
  checks retained, separate owner-selected counts, no claim of automated pass,
  no capped/incomplete speech, and no change to strict profiles or timing policy.
  The owner's 2026-11-01 exception also permits a recording whose only failure
  is measured speech that is too fast: retain the original failed pace evidence,
  accept it for use under `owner-authorized-fast-pace-review-2026-11-01`, and
  expose the separate `too-fast` admin review category. All content, noise,
  ending, pause, punctuation and required timing checks must pass. This is not
  a strict automated pass, and slow/unverified pace or unrelated failures do not
  qualify. Keep playback at 1.0 and preserve the official voice references.

- One-time owner authorization (2026-11-12): run the mandatory production release checks, fix failures and deploy this course release. Repeat only checks affected by fixes. After this deployment, return to quick local iteration without routine tests; this exception does not authorize recurring test runs.
