# HeyHanzi backend

Python 3.12, FastAPI, async SQLAlchemy, PostgreSQL 16 and Alembic. The backend includes cookie-based authentication, transactional emails,
local HTTPS, a migration bootstrap container and a manual Fly release workflow.
See [the frontend authentication API](docs/auth-api.md) to integrate the app.

## Start locally

Docker with Compose v2, Make, and [mkcert](https://github.com/FiloSottile/mkcert)
and Python 3 are required. Docker development only uses Python's standard library
to generate local secrets; use Python 3.12 for host tooling/tests.

```bash
# Once per computer: install/trust mkcert's local CA (may need sudo).
mkcert -install

# Creates an ignored .env and local certificates, builds, migrates, and starts.
make dev-up
# Or detach and wait until healthy:
make dev-up ARGS="-d --wait"
```

Open **https://localhost:8002/health**, **https://localhost:8002/ready**, or
**https://localhost:8002/docs**. No `curl -k` or browser security exception is
required when the mkcert CA is trusted.

```bash
curl https://localhost:8002/health
# {"status":"ok","service":"heyhanzi-backend","deploymentTimestamp":"local","releaseSha":null}
curl https://localhost:8002/ready
# {"status":"ready"}
```

For the local API hostname, run `make local-https` once. Its `add-hosts`
target needs permission to add `127.0.0.1 local-api.heyhanzi.com` to `/etc/hosts`.
Then use **https://local-api.heyhanzi.com:8002**. Authentication cookies require
the frontend and API to be on the same site: use this hostname with the existing
`local.heyhanzi.com` frontend. `make dev-up` health checks also work through
localhost without editing system hosts. Certificates contain both names.

The frontend already runs at `https://local.heyhanzi.com:3002`. Set its ignored
`.env.local` to the backend origin, then restart its dev server:

```dotenv
VITE_API_ORIGIN=https://local-api.heyhanzi.com:8002
```

For local jump-ahead development, set `VITE_DEV_UNLOCK_PATH=true` in the frontend's
ignored `.env.local`. With this backend in `DEV`, the frontend sends
`X-HeyHanzi-Development-Preview: 1` only to `POST /learning/guest/lesson` and
`POST /learning/course/guest`. These read-only guest handlers unlock authored
notebooks, grammar displays, strokes and exercises without adding prerequisite
visits, mastery, billing records or account progress. Actual answers still use
the ordinary grader and content fingerprints. Preview runs stay in frontend
memory and carry `developmentPreview: true`; normal guest requests and imports
reject marked progress. The header is rejected outside `DEV`, on account/import
routes, and for unsupported values or methods. Removing the frontend flag and
restarting its development server restores ordinary learner behavior.

The sibling frontend's example currently points to port 8000; override it as
above. HeyHanzi defaults to **8002 for HTTPS and 5433 for Postgres** to coexist with other local services. Set `BACKEND_PORT` / `POSTGRES_PORT` in `.env` to
change the host ports. If changing Postgres's host port, also update the host-side
`DATABASE_URL` and `TEST_DATABASE_URL`. Containers always connect internally to
`postgres:5432`. Only loopback host interfaces are published.

`make dev-down` stops the project and retains its named database volume.
Application code is bind-mounted and reloads when `app/` changes. Rebuild after
dependency or image changes. A stopped `database-bootstrap` container with exit
code zero is expected. It must finish before the API starts.

## Familiar commands

| Command | Purpose |
| --- | --- |
| `make env` | Create/update `environment/` and install locked runtime packages |
| `make dev-requirements` | Install host test/lint tools |
| `make requirements` | Resolve `requirements*.in` into pinned `requirements*.txt` |
| `make certificates` | Generate certificates if absent and copy the public local CA |
| `make add-hosts` / `make local-https` | Configure the custom API hostname |
| `make dev-up` / `make dev-down` / `make dev-logs` | Run, stop, inspect the local stack |
| `make db-up` / `make db-down` / `make db-logs` | Run, stop, inspect local Postgres |
| `make create-db` / `make ensure-db-exists` | Verify the already-provisioned database |
| `make migrate` | Apply migrations from host Python |
| `make migration MSG="add learners"` | Generate an Alembic revision |
| `make migration "add learners"` | Alternative syntax |
| `make test` | Start local Postgres and run all behavior tests |
| `make test-parallel` | Compatibility alias; the small initial suite runs together |
| `make ci-core` | Lint, architecture/test policy, and tests without Postgres |
| `make verify-local-full` | Core checks and real-Postgres tests |
| `make release-preflight` | Core checks and Python compilation |
| `make release-schema` | Real migration, readiness and transaction tests |
| `make release-container` | Audited image build plus live 768 MiB course/auth container verification |
| `make security-env audit-runtime-security` | Isolated dependency audit tooling |
| `make release-target-check` | Validate supplied production configuration |
| `make release-check` | Complete applicable local release checks |

Host development setup:

```bash
make env dev-requirements
make test
```

The local stack contains the API, PostgreSQL, a migration bootstrap and Mailpit.
Inspect development emails at **http://localhost:8026**. Production uses AWS SES.
`create-db` verifies access; Compose and Render provision their databases.

`make resetdb CONFIRM=RESET-DEV-heyhanzi-dev` is deliberately destructive to this
project's disposable local database. It stops the local API, recreates only the
database inside this Compose project, migrates, and restarts. It accepts no
remote database URL. Production reset is never supported.

## Code and database structure

```text
app/
  main.py                 # create_app, lifespan, middleware and route registration
  config_loader.py        # APP_CONFIGS / .configs.json and explicit env overrides
  environment.py          # configuration validation and origin allowlist
  routers/                # thin HTTP entry points
  schemas/                # Pydantic API contracts
  auth/                   # sessions, email credentials, OAuth and account security
  email/                  # branded templates, SES/SMTP and encrypted delivery queue
  security/               # password policy, hashing, request bounds and rate limits
  services/               # business operations (ready for feature work)
  models/                 # SQLAlchemy models registered for Alembic
  database/               # Base, async sessions, pools, bootstrap ownership
  observability/          # JSON logging and request IDs
  utils/                  # shared infrastructure utilities
alembic/versions/         # reviewed schema migrations
scripts/                  # local, CI and release operations
tests/                    # public HTTP/CLI behavior and real PostgreSQL tests
.github/workflows/       # bounded core CI and manual production deployment
```

The repository intentionally uses an **`app/` source layout**, not a second
`src/` package. Future routers call services; services operate on injected
`AsyncSession`s via `app.database.database.get_db`. Commit explicitly. The
dependency rolls back failed work and always returns connections to the pool.
There is no automatic request/transaction retry.

Import new ORM models from `app/models/__init__.py` before generating migrations.
Review autogenerated migrations, especially renames and drops. The initial baseline establishes migration history. The auth migration adds
users, identities, sessions, one-time tokens, trusted devices, audit events,
shared rate limits, OAuth attempts and the encrypted email queue. Learning
tables will arrive with their features. API startup never changes the schema. Compose's one-shot
bootstrap and Fly's release command run Alembic with an advisory lock and bounded
lock/statement timeouts. Production rollback uses compatible images/forward fixes,
not automatic schema downgrades.

`/health` (and its `/` alias) reports process liveness and release identity.
`/ready` checks PostgreSQL and the release's Alembic heads, returning 503 within a
bounded timeout when unavailable or mismatched. Liveness remains usable during
a database outage. OpenAPI docs are enabled only in development.

## Configuration and deployment

Local defaults live in tracked `.configs.json`; `.env` contains ignored local
overrides. Production reads the validated `APP_CONFIGS` JSON document from Fly
secrets. There is no production fallback to local config or dotenv.

Production's source of truth is encrypted AWS Parameter Store:
**`/HeyHanzi/Env/PROD/Configs`**, region **`us-east-1`**. Auth signing, audit and
outbox encryption keys are unique to HeyHanzi. AWS credentials authorize SES
from `no-reply@heyhanzi.com`. Config is loaded during deployment and staged on
Fly; SSM is never contacted on an API request.

See [deployment setup and recovery](docs/deployment.md) for Render connection,
GitHub secret names, Fly provisioning, custom DNS and rollback steps.

The prepared Fly configuration is **one shared CPU / 768 MiB**, one API process,
no standby machine, and one machine kept warm for reliable email retries. The
release migration machine has the same memory limit. The initial 256 MiB budget
became insufficient once the real course catalog reached approximately 95 MB:
isolated loading measured 531 MiB peak RSS and 275 MiB retained RSS. This is a
measured configuration change, not a production deployment or a capacity claim.
The complete release-image rehearsal measured 564 MiB peak API RSS and 358 MiB
steady RSS with no OOM events under 768 MiB. The release check runs repeatable migrations and real authenticated/guest course
state and detail requests under the Docker limit, retaining API RSS and cgroup
peak measurements in `.deployment-reports/runtime-memory.json`.

The foundation is stateless and supports adding API replicas, with a default
connection budget of three per process (two pooled plus one overflow). Millions
of users will require measured capacity planning: load tests, database indexes,
provider connection budgets, caching and background jobs as features arrive.
Adding machines also adds pools; keep total connections within Render's limit.
The durable email queue is drained by a small background loop in each API
process; leases prevent competing replicas from claiming the same message.

## Verification

Behavior tests cover HTTPS-compatible CORS/preflight, request IDs, development
docs, fail-closed production configuration, healthy/unavailable/stale-schema
readiness, repeatable migration and data preservation, local baseline downgrade /
upgrade rehearsal, rollback with pool recovery, authentication and account
security, OAuth signatures/claims/state, CSRF, token expiry/replay, concurrent
password resets, shared rate limits, and email delivery/retry. Database tests create and
drop only their own unique databases on explicitly local Postgres.

Pushes and PRs run `make ci-core`. The manual production workflow requires master,
runs every applicable release check, exports the tested Docker image, and deploys
that exact image. The image build fails for known Python dependency advisories;
audit tooling stays outside the runtime image. Secrets, certificates and local
environments are excluded from Git and image build context.

## Learning warm-up (first prototype)

Migration `0004_learning_warmup` adds five tables: versioned `learning_tracks`, ordered `learning_stages`, `learning_word_blocks`, account-owned `learning_enrollments`, and `learning_word_completions`. It seeds one Mandarin simplified track and its 20-word warm-up from the frozen `alembic/data/0004_warmup.json`. There are no startup migrations or content writes on GET requests. Use a forward migration for content revisions; preserve published block IDs/order or create a new track version.

Each word block shares text, localized translations and pronunciation fields. Its `language_data` JSON contains a language-specific shape; the initial Mandarin shape (`app/schemas/learning.py`) includes pinyin, a character list, and optional character stroke metadata. Migration `0018_all_character_strokes` connects real geometry for all 1,940 supported characters, replacing the former illustrative 人 reference and covering multi-character blocks such as 中文. This supports words with multiple characters and leaves other languages free to define their own metadata. Warm-up is a separate stage kind with no HSK level. `framework` and stage `level` allow later language-specific level systems; choose the HSK edition before publishing level milestones.

| Endpoint | Behavior |
| --- | --- |
| `GET /learning/tracks` | Public language/track choices, with localized names. |
| `GET /learning/enrollments` | Current verified user's enrolled track IDs. |
| `POST /learning/enrollments` | `{ "trackId": "mandarin-simplified-v1" }`; enroll idempotently and return the path. |
| `GET /learning/tracks/{track_id}/path` | Ordered stages, lightweight word blocks, completion count and next block. |
| `GET /learning/strokes/{language_tag}/{character}/{version}` | Exact character geometry and provenance, fetched when the notebook opens. Verified session required. |
| `POST /learning/tracks/{track_id}/blocks/{block_id}/complete` | `{}`; visit the next word, then return the updated path. Revisiting a completed word is idempotent. |

Path block status is `completed`, `next`, or `locked`. Mutations require the existing Origin, secure session cookie, JSON and CSRF protections. Personal learning responses are `no-store`; unverified accounts cannot access learning progress. The service locks each enrollment while advancing, preventing double advancement across tabs. No caller-supplied user ID is accepted. Counts represent visits in this prototype, not assessed knowledge.

Email signup accepts optional `learningTrackId`; the enrollment is created in the same transaction as the account. OAuth signup accepts `learning_track_id` on the start URL, validates it, and stores the choice in encrypted OAuth state until account creation. Existing users can enroll from the learning page. A repeated signup never alters an existing account's enrollment.

Future stories should declare word-block prerequisites and be checked against exactly that learned vocabulary before publication. Story generation, levels, speech assessment and reviewed handwriting are not implemented in this phase. Vocabulary selection is a common beginner starter set, not an exact frequency ranking or an official HSK level; see [vocabulary reference](https://www.thatsmandarin.com/hsk-1-wordlist/).


### Character stroke metadata

Migration `0018_all_character_strokes` adds 1,937 immutable payloads from the existing Dong Chinese Modern Regular geometry snapshot and references all 1,940 supported characters across warm-up and HSK 1–6. It reuses the exact published 我/你/好 versions, replaces the illustrative 人 reference, and preserves all historical resources, word IDs, other metadata and learner progress. Multi-character blocks receive one reference per distinct character in text order. The migration validates content hashes and imports in bounded batches; API responses still carry only references and geometry remains authenticated, on demand and `no-store`.

The frozen [manifest](alembic/data/0018_strokes/manifest.json) records the inventory and canonical resource hashes. New payloads identify the complete local source snapshot by SHA-256, the original page by SHA-256, and the canonical strokes/medians by SHA-256. Geometry and ordering are unchanged community data; these new records do **not** claim per-character official-standard verification. The frontend redistributes byte-identical payloads at `/stroke-data/<lowercase-codepoint-hex>.json`, with the existing Arphic license. The three earlier reviewed characters retain their original provenance and download URLs. Run the normal migration/bootstrap command for local review; production remains a separate manual release.

`0005_wo_stroke_order` updates only 我’s `language_data.strokeOrder`, preserving block IDs, enrollment and completion records. It uses the frozen `alembic/data/0005_wo_strokes.json`. Migration `0006_character_strokes` subsequently extracts both real geometry and illustrative samples into `learning_character_strokes`, keyed by language/region, character and SHA-256 of the full canonical payload. Word JSON retains only `StrokeReference` objects; path, enrollment and completion responses contain no geometry. `GET /learning/strokes/{language_tag}/{character}/{version}` returns `{reference, data}` for that exact version and requires a verified session. Missing characters, language variants and versions return 404; responses remain `no-store`. There is no third-party fetch. `StrokeAnimation` documents ordered SVG outlines, paired median trajectories, format, per-character identity, source revision/hash/license, and the reviewed standard reference. `StrokeSample` stays compatible for illustrative prototypes; other language metadata need not include strokes.

The authority for simplified Mandarin ordering is [GF 0023-2020](https://www.moe.gov.cn/jyb_sjzl/ziliao/A19/202103/t20210318_520473.html) (8,105 characters). 我 has seven strokes, `3121534`, on printed page 44 / PDF page 50. Animation geometry is community data from [Make Me a Hanzi / Hanzi Writer Data](https://github.com/chanind/hanzi-writer-data), pinned to `68d10a4b21150cae5e1ebbd223eed289cf32d90c`; this is not a government-certified geometry dataset. Future characters need a coverage/ordering review, and future Japanese tracks need their own ordering source.

Stroke geometry is unchanged; format/provenance metadata was added on 2026-10-11. The Arphic Public License and copyright are retained in `alembic/data/licenses/ARPHICPL.txt`. The frontend distributes the same metadata publicly at `/stroke-data/wo.json` and the license at `/licenses/ARPHICPL.txt`. Keep that redistribution copy synchronized when adding future frozen character revisions. Stroke data retains its own license separately from application code.


The frontend shares pending requests and keeps a bounded application-scoped character cache. New character revisions must insert a new immutable resource and update word references through a forward migration; retain previous versions. Region is part of the resource language tag (`cmn-Hans-CN` for current Mandarin), so future Japanese data cannot collide with Chinese geometry for the same Unicode character. The migration moves geometry without rewriting enrollment or completion records, and its downgrade restores the original inline metadata for local schema rehearsals.

### Adding another character

Use this workflow for the next reviewed Mandarin character. The geometry supplier is **Hanzi Writer Data**, derived from **Make Me a Hanzi** and Arphic font outlines. The renderer is our React SVG player; `hanzi-writer-v1` names the compatible data format and does not mean the Hanzi Writer JavaScript library is installed. The [pinned upstream README](https://github.com/chanind/hanzi-writer-data/blob/68d10a4b21150cae5e1ebbd223eed289cf32d90c/README.md) records the data's origin and separate license.

#### Reproducible source for 我

| Item | Recorded value |
| --- | --- |
| Supplier revision | `68d10a4b21150cae5e1ebbd223eed289cf32d90c` |
| Raw geometry | [我.json at that revision](https://raw.githubusercontent.com/chanind/hanzi-writer-data/68d10a4b21150cae5e1ebbd223eed289cf32d90c/data/%E6%88%91.json) |
| SHA-256 of downloaded bytes (`source.sha256`) | `08616462fc64b4c18c76a3f68a992305e98946f468bf42ec76ca9be1cd6c5ac8` |
| HeyHanzi resource version | `591b1e24fd59a28e4681af9078488e6f0ade958bd9c55cac6940a938178fcb07` |
| Ordering variant | `cmn-Hans-CN` |
| Reviewed ordering | GF 0023-2020, printed page 44 / PDF page 50, seven strokes, `3121534` |
| Frozen application payload | [alembic/data/0005_wo_strokes.json](alembic/data/0005_wo_strokes.json) |
| Retained license | [alembic/data/licenses/ARPHICPL.txt](alembic/data/licenses/ARPHICPL.txt) |

The raw-file checksum and resource version are different on purpose. `source.sha256` identifies the exact downloaded upstream bytes. `version` hashes our complete payload, including geometry, provenance and review metadata. `source.revision` identifies the supplier's Git commit; it is neither of these hashes.

#### Import and review

1. **Fetch just the intended character from a pinned commit.** Use `https://raw.githubusercontent.com/chanind/hanzi-writer-data/{revision}/data/{URL-encoded-character}.json`. For example, 你 is `%E4%BD%A0`. Start with the revision above; if the character is absent or needs a corrected newer version, review that upstream revision explicitly and record it. A missing file is missing coverage, not an empty stroke sequence. Do not use `master` or `latest` as a stored source, or call the supplier during API requests.
2. **Check both shape and ordering.** `strokes` holds filled SVG outlines in writing order; `medians` holds matching pen trajectories with points in writing direction. Counts must match, every median needs at least two finite coordinate pairs, and every outline must be valid SVG path data. Preserve these arrays and their order. Confirm the glyph, stroke count, order, direction and hooks against the applicable standard, and record the reference/page actually reviewed. Our current player assumes the [Make Me a Hanzi coordinate system](https://github.com/skishore/makemeahanzi#format): x spans 0–1024, y runs upward from −124 to 900. Its display transform is `translate(0 900) scale(1 -1)`. Japanese and other regional variants need their own reviewed source and language tag.
3. **Prepare a new frozen payload.** Follow `0005_wo_strokes.json`: `format`, `character`, `strokes`, `medians`, `source`, and the reviewed `standard` when available. Record the actual raw-file SHA-256, pinned source URL/revision, copyright, license name/URL, and a dated extraction/modification notice. Set `source.dataUrl` to the new character's downloadable payload, not 我's URL. Validate against `StrokeAnimation` in `app/schemas/learning.py`; retain the original geometry rather than substituting hand-drawn samples. Migration `0018_all_character_strokes` replaces the old illustrative 人 reference with frozen community geometry; its legacy resource remains stored.
4. **Publish through a new forward migration.** Save the payload under `alembic/data/` with a descriptive, revision-specific filename. Compute its resource version below. Insert `(language_tag, character, version, data)` into `learning_character_strokes`, reusing an identical existing resource where appropriate. Update only the intended words' `language_data.strokeOrder` references to `{format: "stroke-reference-v1", languageTag, character, version}`. Preserve all other word metadata, IDs, enrollment and completion records. New characters use the normalized table directly; do not recreate the old inline-geometry phase or edit migrations 0004–0006. Retain old resource versions so existing references continue to resolve.
5. **Keep redistribution and the UI consistent.** Add the corresponding public downloadable JSON and retained license in the frontend; these are attribution downloads, not runtime imports into the JavaScript bundle. Keep the public JSON equal to the frozen backend payload. Public downloads use `/stroke-data/wo.json`, `/stroke-data/ni.json`, `/stroke-data/hao.json` and `/stroke-data/<lowercase-codepoint-hex>.json`. Preserve their Node/Nginx static routing and verify downloads in SSR, dev and static builds. Keep the license notice and links visible in the notebook. The current attribution wording assumes Make Me a Hanzi/Arphic; update it if a different provider is introduced. The [pinned Arphic Public License](https://github.com/chanind/hanzi-writer-data/blob/68d10a4b21150cae5e1ebbd223eed289cf32d90c/ARPHICPL.TXT) governs the graphics separately from application code; retain its text unchanged and document modifications. Dictionary data is a separate source/license and is not part of this geometry import.

Compute the resource version exactly as migration 0006 does, after finalizing all payload fields:

```python
import hashlib
import json

# `payload` is the complete parsed HeyHanzi JSON object, not the raw upstream file.
canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
version = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

Do not hash pretty-printed JSON or only the stroke arrays for `version`. Changes to provenance, review notes or download URLs also create a new resource version.

#### Verify before adding more

Run `make ci-core` and `make release-schema` in the backend. In the frontend, run `yarn lint`, `yarn build:static`, `yarn test`, and relevant learning browser checks. Keep additional tests focused on observable behavior: lightweight path/enrollment/completion responses, exact-version lookup and missing-version handling, first-open fetching, cached reopen, and retry. Inspect the actual animation on desktop and mobile for stroke direction, clipping, crossings and the completed glyph; preserve a non-sensitive screenshot under `artifacts/learning/strokes/`. Check reduced-motion stepping and downloadable data/license links. Apply the forward migration to local development for review; production releases follow the existing manual workflow.

### Pronunciation audio

**Current production policy:** use [the offline two-voice pipeline](docs/audio-production.md)
for new audio. It generates adult female and male variants, qualifies them
automatically, retains MP3s and metadata only, and activates a complete verified
release. Human listening follows on the website; it is not a generation gate.
The API overlays the active CDN references without changing learner progress.
The individual migrations and voice experiments below are historical provenance,
not settings or retention instructions for new recordings.

Migration `0007_wo_audio` adds only an `audio-reference-v1` reference to 我’s existing `pronunciation` JSON; it preserves enrollment, completion and stroke data. References identify the language variant, a particular word reading, encoded-audio SHA-256 version, MIME type and byte length. This common pronunciation structure is independent of Chinese-specific stroke metadata.

The initial recording is `FunAudioLLM/Fun-CosyVoice3-0.5B-2512`, generated through [the publisher's demo](https://huggingface.co/spaces/FunAudioLLM/Fun-CosyVoice3-0.5B) in zero-shot mode with its supplied reference voice, seed 42, speed 1.0, and input `我。`. It is the isolated take generated during the initial comparison; a later regeneration attempt hit the public demo quota. `app/data/pronunciation/wo-wo3.json` records the original WAV checksum, reference recording/transcript, demo revision, model link/license and processing. The hosted demo does not expose its running weight revision. The model checkpoint is Apache-2.0; that statement does not independently establish rights to every reference voice. This development recording awaits native-speaker review.

The 8,685-byte MP3 ships in `app/data/pronunciation/` under its content hash. `GET /learning/audio/{language_tag}/{audio_id}/{version}` requires a verified session, resolves only explicitly registered assets and streams bytes with `audio/mpeg` and `Cache-Control: no-store`. The frontend performs bounded session-memory caching after opening the word card, including preparation for immediate local playback. No synthesis dependency or model runs in the API process, and no frontend bundle contains the audio dataset.

To add another reading: generate/record the exact target reading with documented voice rights, have a Mandarin reviewer check it, encode an immutable audio file, record provenance alongside it, register its manifest in `app/services/pronunciation.py`, and add a forward migration attaching the small reference to the relevant word's pronunciation. Keep existing hash-addressed files available; never replace bytes under an existing version. A future object-storage/CDN adapter can replace file delivery without changing word metadata or the frontend download boundary. Keep audio for isolated words separate from sentence/story audio, since tone changes and phrasing depend on context.

#### Female voice generation for 我

Migration `0008_wo_slow_audio` selects the 1.4-second, 17,901-byte clip documented in `app/data/pronunciation/wo-wo3-adult-slow.json`. The previous file and reference remain available. Only 我’s audio reference changes; the endpoint, on-demand download behavior, progress and stroke metadata are unchanged.

This clip was generated locally with **CosyVoice 3**, code revision `074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc` and model revision `29e01c4e8d000f4bcd70751be16fa94bf3d85a18`, using `inference_instruct2`, seed **46**, model speed **0.6**, and `text_frontend=False`. Input `[w][ǒ]。` explicitly supplies the intended pinyin; the instruction is `You are a helpful assistant. 请用尽可能慢地语速说一句话。<|endofprompt|>`. The reference is a four-second **synthetic** female voice from our earlier Qwen3-TTS comparison (Serena), not a recording of an identified person. CosyVoice generates the final word. Exact reference provenance and hashes are in the manifest.

The voice target is an adult female Mandarin teacher with slow, clear isolated-word pronunciation. Several candidates were screened for duration and approximate pitch contour. The selected take has a fall and rise in its estimated pitch; it still **awaits native-speaker assessment** of pronunciation, naturalness and perceived voice age. This is a development selection, not a certified teaching recording. Keep isolated-word tone demonstrations separate from connected speech, where third-tone realization and tone sandhi differ.

The frontend sibling keeps the source WAV, selected MP3, candidate metadata, acoustic check, pinned Python dependencies and `generate-pinyin.py` under `artifacts/learning/audio/adult-female-slow/`. To reproduce it, use a separate Python 3.11 environment, install `generation-requirements.txt` (CPU Torch wheels require `--extra-index-url https://download.pytorch.org/whl/cpu`), check out the pinned CosyVoice source with its Matcha-TTS submodule, and download the pinned model with Hugging Face `snapshot_download`. Required model files are `cosyvoice3.yaml`, `campplus.onnx`, `flow.pt`, `hift.pt`, `llm.pt`, `speech_tokenizer_v3.onnx` and `CosyVoice-BlankEN/*`. Keep Hugging Face’s local download metadata; the script verifies the revisions. Run `python generate-pinyin.py --cosyvoice-source /path/to/CosyVoice --model-dir /path/to/model`; take seed 46. Encode its WAV with `ffmpeg -i wo-cosy-slow-pinyin-46.wav -map_metadata -1 -codec:a libmp3lame -b:a 96k -ac 1 output.mp3`. No post-generation pitch/speed adjustment or browser playback-rate change is applied. Keep the model environment and multi-gigabyte weights outside the API image and frontend bundle.

#### Previous pace: 30% faster

Migration `0009_wo_audio_tempo` selects the same female CosyVoice take with a **1.3× tempo** adjustment, preserving pitch. `wo-wo3-faster.json` records the new immutable hash, source-take provenance and processing. Both previous audio versions remain available. To reproduce it, apply `ffmpeg -i wo-cosy-slow-pinyin-46.wav -af atempo=1.3 -map_metadata -1 wo-cosy-30-percent-faster.wav`, then encode that WAV to MP3 with the same 96 kbps mono settings. Always start from the original lossless WAV rather than repeatedly processing MP3s. The browser still plays at 1.0×; no frontend behavior or on-demand download contract changes.


#### Current voice: young adult female, another 20% faster

Migration `0010_wo_adult_voice` selects `wo-wo3-young-adult.json`: a **new CosyVoice 3 generation**, guided by AISHELL-3 speaker **SSB0534** (female, northern accent, documented age group **C: 26–40**). The short reference `SSB05340002.wav` establishes vocal characteristics only. No recorded-word dataset is queried or concatenated to produce app speech; the same CosyVoice model/reference can generate entirely new sentences and stories. For connected speech, use normal sentence text and appropriate pace rather than this isolated-word tone instruction.

The [publisher’s AISHELL-3 dataset](https://huggingface.co/datasets/AISHELL/AISHELL-3) declares Apache-2.0. The manifest pins its revision, reference checksum/transcript, speaker metadata and source URL. License and attribution are retained in `app/data/pronunciation/licenses/`; the unmodified reference and dataset metadata are in the frontend’s `artifacts/learning/audio/mature-female/`. The older synthetic reference sounded youthful and is superseded. Speaker metadata establishes the reference’s age group, not a guarantee of the generated voice’s perceived age.

Generation uses the same pinned CosyVoice code/weights/environment documented above, `instruct2`, `[w][ǒ]。`, seed **46**, speed **0.936**, and the slow-speech instruction. In the frontend artifact directory, run `generate.py --cosyvoice-source /path/to/CosyVoice --model-dir /path/to/model`, then `prepare-selected.py`. The latter uses pitch-preserving time stretching on the new lossless take to match the preceding clip’s duration divided by **1.2**, yielding **0.91075 seconds**, and applies fixed gain without clipping before encoding a **11,853-byte** MP3. The manifest records all processing, hashes and source settings. Acoustic screening shows a falling/rising pitch contour; native-speaker assessment of pronunciation and naturalness remains pending.

All three older immutable audio versions remain available. The migration changes only 我’s audio reference, preserving learning progress and stroke metadata. Delivery remains authenticated and on demand when the notebook opens; models and voice-conditioning references stay outside frontend bundles and the API runtime.


#### 你 and 好: geometry and pronunciation

Migration `0011_ni_hao_media` adds versioned media references to the existing `mandarin-simplified-v1-warmup-ni` and `mandarin-simplified-v1-warmup-hao` blocks. It inserts immutable geometry into `learning_character_strokes` and changes only each word's stroke/audio metadata. Existing translations, IDs, progress, and 我 assets are preserved. Its local downgrade removes the new word references while retaining the immutable resources.

| Character | GF 0023-2020 ordering reviewed | Frozen geometry | Public attribution download |
| --- | --- | --- | --- |
| 你 | Seven strokes, `3235234`; printed p. 45 / PDF p. 51 | `alembic/data/0011_ni_strokes.json` | `/stroke-data/ni.json` |
| 好 | Six strokes, `531521`; printed p. 32 / PDF p. 38 | `alembic/data/0011_hao_strokes.json` | `/stroke-data/hao.json` |

The geometry comes unchanged from Hanzi Writer Data revision `68d10a4b21150cae5e1ebbd223eed289cf32d90c`, with raw SHA-256, license, review reference and extraction notice recorded in each payload. The frontend's public copies must remain identical to these frozen files. The existing Arphic license applies. Ordered medians and strokes were checked against the standard, and both completed glyphs and animations are exercised in desktop/mobile browser checks.

Audio manifests are `app/data/pronunciation/ni-ni3-young-adult.json` and `hao-hao3-young-adult.json`; migration references are frozen in `alembic/data/0011_{ni,hao}_audio.json`. Both are new CosyVoice generations using the same pinned code/weights, AISHELL-3 SSB0534 voice-conditioning sample and processing settings as 我. Inputs explicitly specify `[n][ǐ]。` and `[h][ǎo]。`; selected seeds are 46 and 45 respectively, model speed 0.936. Pitch-preserving tempo factor 0.9662366181718364 and gain 3.0 match 我's current processing. The outputs retain natural per-syllable durations of 1.055625 s and 1.0970416667 s; neither is forced to match another syllable's length. Isolated nǐ and hǎo are third-tone demonstrations; generating the phrase 你好 later requires context-sensitive tone sandhi, not concatenating these isolated clips.

To reproduce, use the isolated generation environment documented above and the frontend's `artifacts/learning/audio/ni-hao/generate.py`, then `prepare-selected.py`. The scripts reuse the existing reference WAV in `../mature-female/`; they never run in the API or browser. Candidate WAVs, processing settings and approximate pitch checks are retained for review. All generated pronunciation remains awaiting native-speaker assessment. Delivery remains authenticated, versioned, and on demand through the existing stroke/audio endpoints.

## Character-first curriculum prototype

Migration `0012_learning_curriculum` extends the Mandarin track without replacing any existing warm-up IDs, enrollments, completions, or media versions. The 20-word warm-up covers 21 distinct characters. Six level records follow the pinned 2025 HSK recognition lists; the remaining character introductions are split into units of at most ten. Every five introductions and each shorter unit's end has a planned reading marker. These markers are optional and do not represent real reading lessons or vocabulary mastery.

The language-neutral level/unit/activity models retain localized names and stable sequences. Mandarin adds normalized character prerequisites for each activity and lexical entry. `learning_vocabulary` stores separate readings and meanings, with its own language tag; dictionary eligibility is not a completed-word record. Other language tracks can supply different levels and word metadata without being assigned HSK or character prerequisites.

Authenticated, `no-store` endpoints:

- `GET /learning/tracks/{id}/journey?level=hsk-1&limit=20`: level totals plus a bounded activity window; optional `around={block}`, `before={sequence}` or `after={sequence}` selects an anchor/direction. Maximum limit is 40. These anchors are mutually exclusive.
- `GET /learning/tracks/{id}/blocks/{block}/vocabulary`: paginated common vocabulary containing the selected character, with no unknown character prerequisites. `after={vocabulary-id}` requests another page. A locked future character cannot unlock vocabulary. `newlyAvailable` identifies combinations first made possible by the selected introduction.
- Existing completion routes remain enrollment-locked and idempotent. A planned story can be visited only after its prerequisite introductions, without moving the core word frontier. The legacy `/path` and completion responses remain limited to the original warm-up for earlier clients.

The frozen seed is `alembic/data/0012_curriculum.json`. It uses `zshanhui/hsk-v3` revision `182692ce5a11bc30bdc771835d2f0f27491c25de`, the 2025 HSK character/vocabulary lists, and the recorded CC-CEDICT snapshot. `0012_sources/sources.json` retains URLs and hashes; raw HSK inputs and license/NOTICE copies are retained alongside it. The initial import has 5,370 readings/entries; forward migration `0013_vocabulary_readings` consolidates equivalent pinyin spacing to 5,366 entries and corrects the neutral second syllable of 老婆. The repaired set is not a claim of matching every numbered examination entry. Character introductions total 1,919 after the preserved warm-up; readings, gloss selection, curriculum ordering and lesson design remain editable prototype content.

Rebuild the frozen seed explicitly with `environment/bin/python scripts/curriculum/build_hsk_seed.py --dictionary /path/to/the/recorded/cedict.txt.gz`. The script checks the dictionary archive's recorded hash. Runtime requests do not download or parse source datasets. Published changes must use a new forward migration and preserve stable learner records. Downgrade is permitted only for disposable TEST database rehearsals, never development/production progress.

The adapted dataset is CC-BY-SA-4.0, separately from application code. The frontend retains the required attribution and full license in its existing combined legal download, and offers the adapted JSON as an explicit redistribution download. No dictionary, full curriculum, stroke geometry or audio bytes are embedded in journey responses. Before adding real stories, persist vocabulary introductions/mastery separately and validate stories against explicitly introduced vocabulary and grammar.

## Annual subscriptions

HeyHanzi billing supports Free (all warm-up content) and Unlimited (USD 1.99/month billed yearly at USD 23.88 plus tax, HSK stroke order and stories). Migration `0017_billing` stores server-owned customer/subscription state. Checkout, customer-portal sessions and signed Stripe webhooks live under `/billing`; paid learning requests are authorized on the backend. See [Stripe setup and release inputs](docs/billing.md) for provisioning, secret configuration, tax choices, sandbox verification and lifecycle behavior. Setup does not deploy or charge a customer.
