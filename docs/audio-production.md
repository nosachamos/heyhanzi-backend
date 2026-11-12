# Audio production policy

HeyHanzi generates complete utterances with adult female and adult male Mandarin
voices. The acceptance target is clean, naturally paced standard Mandarin with
complete pronunciation, no unwanted leading/trailing noise and no abrupt playback
boundaries. Every content item requires both voices before a release is complete.
The initial default is female; a site-wide voice picker is a future UI feature.

## Owner-directed catalog closure (2026-11-01)

The owner explicitly authorized closing the current two-voice catalog using the
best retained recordings, including known imperfect recordings, and requested an
Audio Review development dashboard. This supersedes the earlier exclusion of a
review dashboard and the publication block for quality-only exhausted items in
this bounded replacement. Preserve all original receipts, scores, failures,
voice references and attempt histories. Ordinary automatic qualification stays
unchanged. Record known quality exceptions separately under
`owner-authorized-known-quality-exception-2026-11-01` and show them in red;
previous `owner-authorized-best-available` selections are yellow. Neither is a
human review or an automatic pass. Do not fabricate word timings. Missing actual
media, wrong voice identity, corrupt bytes or incomplete synthesis still block
closure. If an item has no retained usable media, generate one bounded final
attempt and retain usable output with its real metrics and appropriate warning;
there is no automatic retry tail. Activate only the complete verified catalog;
production deployment remains a separate manual action.

## Scope and acceptance

Production is a finite offline GPU command, never an API worker, queue, or runtime
synthesis request. Automated checks decide acceptance; human listening follows on
the website and is not an approval dependency. `automated-pass` does not claim
human review or guarantee perfect tones/naturalness. Do not weaken checks just to
fill catalogue gaps. Exhausted retries remain rejected and block publication.
No review dashboard or candidate-management application is in scope.

The two profiles in `app/data/sentence-audio/voices/` pin model/code revisions,
reference identity and checksum, language, synthesis mode, seed, encoding and the
complete quality policy. Do not reintroduce the old "as slowly as possible"
instruction, global post-generation time stretching, or fixed triple gain.
Playback stays at 1.0. Generate connected phrases; never concatenate recordings
of individual characters. Retain Mandarin contextual tone sandhi. Use explicit
pinyin controls for authored lexical readings and the few documented contextual
misreadings. Leave unambiguous 一/不 prefixes native inside controlled compounds
so their connected-speech tone changes remain natural.

### Current official Mimi and Bao references

The owner selected the two **rainy-day v1 generated MP3s** as the official voices
for Mimi and Bao and authorized a fresh two-voice regeneration run. This is a new
selection, not a resumption of the cancelled candidate-A batch below. Do not
substitute the original WhatsApp files, the later male fluent-v2 experiment, or
candidate A for the selected conditioning clips.

- Bao: `bao-rainy-day-v1` on the compatible `mandarin-male` channel, using
  `Rainy-day-missed-bus-male.mp3`, SHA-256
  `1e726d364b0a804627a148b3ae7d0fb8c2dadee1943a041b40c35b0a3805acfc`.
- Mimi: `mimi-rainy-day-v1` on `mandarin-female`, using
  `Rainy-day-missed-bus.mp3`, SHA-256
  `747ff92ddf39d23bbf126a06287d4469448ade5fa574ef5bc2218cb871ccf8db`.

The exact MP3 conditioners and their verified source provenance are pinned under
`artifacts/audio-official-rainy-day-v1/`; `voices/male.json` and `female.json`
include the approved reference transcript, checksum, original receipt hash,
source voice recording provenance, model/code revisions and generation settings.
New utterances use complete connected synthesis, natural pitch, speed 1.0, and
normal bounded retries. `requiredPace: regular` records this requirement; invoke
production with `--pace regular` and verify the final delivery report for **every**
utterance, including cached and owner-selected recordings. The existing clean-v5,
full-content, punctuation, ending and observed-timing rules remain unchanged.
These profiles do not inherit the cancelled Bao candidate-A-specific cadence gate.

The owner's 2026-11-01 pace-review exception permits a recording whose only
failed qualification is speaking too fast to be made available with the distinct
`too-fast` warning and `owner-authorized-fast-pace-review-2026-11-01` selection
policy. This is an explicit owner availability exception, not a normal automated
pass, a human approval, or a change to the pinned voice/quality recipes. Retain the
original pace measurements, failed checks, MP3, receipt and all provenance.
Content/pronunciation, noise, clean endings, punctuation, pauses, integrity and
required complete timing still have to pass. Do not put an unrelated failure
into this category merely because a fast-pace failure is also present.
`fast_pace_failures_only` in `app/services/audio_catalog_closure.py` recognizes
`regular-pace-too-fast`; an additional clean-v5 `pace-outside-policy` failure
requires its saved measurement to independently prove speech faster than that
recipe's floor. Unknown, slow, missing or contradictory measurements fail closed.
New catalog summaries that contain this category include `paceReviewPolicy`
and a `too-fast` count in `warningCounts`, while retaining the established exact
closure approval and alignment binding. Admin native-speaker approval uses the
same durable recording-key overlay as other warning categories; its original
failure history remains available. This exception needs no ten-failure window
or new human approval before publication, and never changes playback from 1.0.

Media-only catalog promotion must not invalidate saved learner answers or drafts.
The compiled course manifest can include server-owned `media_compatibility`
metadata with per-occurrence revision, current full fingerprint, bounded previous
fingerprints and a semantic SHA. The compiler must prove old/new authored records
are identical after removing only cloze delivery maps, story recordings and story
word audio references. Text, pronunciation, answer keys, prerequisites, cast,
placement, steps and every other authored field remain part of that comparison.
The API independently validates the semantic SHA and exact current identity;
clients cannot supply aliases. Full fingerprints still protect catalog integrity.
The root metadata also pins `learnerPolicySha256` to the exact learner policy;
changing trail placement or lexical prerequisite crosswalks cannot silently reuse
the media-only proof.
Compatible account histories retain their stored original fingerprints and count
toward the same task; public run DTOs expose the current fingerprint for resume.
Compatible guest responses are regraded normally, then canonicalized for storage
and account import. Unknown or substantively changed versions remain archived and
cannot grant current completion. This mechanism requires no database migration.

The former voices are reserved as **Chen** (original male SSB0710) and **Lan**
(original female SSB0666). Their exact original profiles remain in
`voices/archived/chen-original-male-v1.json` and
`voices/archived/lan-original-female-v1.json`, with their retained media/catalog
archives in the sibling frontend's `artifacts/mascots/voice-archive/`. Never delete
or overwrite their profile namespaces, generated recordings or provenance when
adopting the new voices; these names reserve reusable voices, not new active
course personas. The existing active course audio release remains unchanged until
a complete replacement passes import and delivery checks. A selected profile or
successful pilot is not evidence of completed whole-course regeneration.

### Persistent failures: correct the input before spending another retry window

The owner's 2026-10-30 instruction requires an editorial intervention when the
same material repeatedly fails. For new correction batches, run one bounded
ten-attempt window, including the existing explicit best-available selection.
An unresolved item then leaves the generation queue for diagnosis; do not
automatically chain another ten-attempt window or the historical four-attempt
repair tail. This changes the production workflow, not the pinned voice profiles
or acceptance thresholds. Previously retained attempts and selections remain
historical evidence.

Check these remedies in order:

1. Reuse a retained recording with exactly the same Hanzi and intended lexical
   readings when the original receipt, voice, controls, final bytes and regular
   pace verify. Formatting-only pinyin differences may use an explicit alias;
   preserve the original receipt and selection binding. Timing-dependent uses
   additionally require complete measured word alignment.
2. Correct demonstrably wrong pinyin in the authoritative source and its
   annotation rule. Never change a tone merely to match an incorrect recording.
3. Record long narration in complete, contiguous sentence blocks when this
   preserves its text, narrator choice, paragraph/checkpoint boundaries and
   measured playback alignment. Keep dialogue speaker turns connected. Test a
   representative two-voice pilot before expanding a segmentation change.
4. Rewrite a persistently failing sentence only after checking its target sense,
   communicative purpose and vocabulary/grammar available at the existing course
   position. Update dependent answers, glosses and readings where necessary.
   Generic learning-about-the-word sentences and unrelated padding are not fixes.

Record the reason, old/new content IDs, affected placements and validation in the
current campaign's repair ledger. Revised text/readings need matching audio for
both voices. Keep frozen manifests and old recordings intact; use a separately
identified correction batch. A new ID alone is not a substantive correction and
must never reset attempts for unchanged material. If the revised input also
exhausts its bounded window, diagnose it again instead of looping automatically.
Source corrections and their matching media become active together after the
complete replacement passes verification; a stopped old campaign or successful
small repair batch does not establish full-course completion.

### Cancelled Bao voice calibration (historical)

The owner subsequently rejected candidate A as sounding like the existing Bao
voice and explicitly cancelled regeneration. The original male profile and
Mascots references were restored; no replacement release was activated. The
following calibration notes are historical, not authorization to resume. See
`../heyhanzi-ui-web/artifacts/mascots/bao-voice-adoption/CANCELLED.json`.

On 2026-10-25 the owner selected candidate A (SSB0261) for Bao and explicitly
requested its natural phrasing and pauses throughout his speech. The cancelled
`bao-natural-adult-v1` production recipe:  it preserves the
compatible `mandarin-male` channel, conditions on the exact approved candidate-A
MP3 at natural pitch and speed 1.0, and begins at its zero-shot seed 258. The new
profile digest distinguishes all cache keys and media from the original SSB0710
voice. `voices/archived/ssb0710-mandarin-natural-male-v1.json` and the frontend's
`artifacts/mascots/voice-archive/bao-original-ssb0710/` retain that original voice
and recordings for an upcoming older male character. Never delete or overwrite
those records while replacing Bao. The source speaker age category is 26–40;
Bao's character age of 28 is an art direction, not a measured speaker age.

The exact approved MP3 has SHA-256
`45c5b9b3496993e7617f8e8f773ef57374559f52ad8f9ae6d3c7bea9dc64b7a6`.
Its observed internal sentence intervals are 0.54–0.72 seconds, with 0.35–0.43
seconds of actual quiet. `scripts.audio_pipeline.cadence` adds the separately
pinned `bao-natural-cadence-v1` screen: regular audible speech pace, the existing
punctuation checks, and internal sentence intervals of at least 0.48 seconds
including at least 0.24 seconds quiet. These lower bounds allow natural variation
around the selected sample; they do not force every pause to an identical length.
Comma, list and semicolon boundaries retain their existing shorter requirements.
Terminal punctuation does not require artificial trailing silence. The ordinary
maximum-pause, full-content and clean-ending gates still apply.

For opted-in profiles, validate `cadencePolicy` and `cadenceImplementationSha256`
with `validate_cadence_profile`. Every final MP3 needs `quality.cadence` from
`assess_cadence`; recheck it using `verify_recording_cadence` on generation resume,
import and publication, including owner-selected recordings. Feed it the actual
final-delivery punctuation/ending timing evidence. It binds the exact PCM, text,
pinyin and policy and remeasures the waveform. The saved accepted flag cannot
override short pauses or stale evidence. Failure requires another bounded
synthesis attempt, never inserted silence, time stretching or invented timings.
The global clean-v5 policy and historical female/old-male recipes stay unchanged.

The original eight-entry pilot and CLI controls are in
`artifacts/audio-bao-cadence/`. Subsequent short-phrase calibration found that
conditioning on the original SSB0261 recording rushed `我们去吧。` in all 24
bounded attempts. Conditioning instead on the exact approved candidate-A MP3
preserved the selected voice and phrasing and passed that phrase in five attempts.
`artifacts/audio-bao-cadence/approved-reference-pilot/validation.json` records all
14 strict passes: the eight original examples plus six short functional phrases.
`好的。` needed 17 attempts, within the normal 24-attempt budget. No quality gate,
pitch, waveform speed or instruction was weakened to obtain those results.
The original raw-reference profile and failed receipts remain archived. The new
reference metadata honestly identifies generated, owner-approved speech and pins
its original SSB0261 source, model, recipe and exact MP3 hash; it is not relabelled
as an original AISHELL recording. The retained conditioning file is
`artifacts/audio-bao-cadence/approved-reference-pilot/Bao-candidate-A.mp3`.
The pilot covers an isolated character, a word, short statements, questions, two
multi-sentence story paragraphs and the approved comparison passage. This is
bounded qualification evidence, not a guarantee that all future speech will
sound equally natural. Bulk replacement remains incomplete until every required
male recording, alignment and reference has been regenerated, verified and
imported; changing the recipe alone does not switch the active audio release.

After every producer for the selected IDs has stopped, `python -m
scripts.repair_bao_audio` can finish a bounded failed batch. Supply one or more
`--manifest` files, `--spool`, `--output`, and the same `--source`, `--weights`,
`--reference`, `--asr` and `--phonetic` arguments as generation; `--ids` optionally
limits the repair to a JSON list of IDs. It preserves failed-attempt snapshots
and uses at most 24 total attempts per utterance: the initial ten, one further
ten-attempt owner-selection window, then up to four strict attempts. It never
resets attempt counts, changes the profile or relaxes cadence. An utterance with
no eligible delivery after that budget remains failed and blocks completion.
Invalid or partial cached recordings also fail closed for explicit diagnosis.
The command never uploads or activates a release.

`python -m scripts.release_bao_audio --spool /path/to/spool --mapping
/path/to/backend-mapping.json --output /path/to/release` performs complete local
preflight and packaging. The mapping covers every original backend content ID and
points to its exact canonical generation receipt. Whitespace/hyphen syllable
separators may differ; Hanzi and actual readings must match. Canonical receipts
retain their original IDs and provenance rather than being rewritten for aliases.
An incomplete batch fails before any upload. `--upload` writes immutable new male
objects and reads every uploaded MP3/receipt back; `--activate` also switches local
metadata after that succeeds. It requires `--upload`, preserves all female
references and old releases, and refuses a concurrent active-release change.
This is not an application deployment.

If any recording uses the existing ten-failure owner exception, the release keeps
`qualified: false` and `automatedAccepted: false`, with separate owner-selected
counts, exact media/receipt hashes and failed-check lists. Runtime availability
requires the explicit owner authorization and consistent complete counts. The
offline publisher still rechecks the full exact-byte owner-selection provenance
and Bao cadence before packaging. The historical female human-approved exception
retains its exact original record. No rejected take is relabelled automated.

### Regular story and dialogue pace

New story and persona-turn production additionally uses
`scripts.audio_pipeline.pace` and the explicit `mandarin-regular-pace-v1` policy.
The current target is **regular**, with natural connected syllables and pauses.
Future slow/fast modes need separately calibrated policies; they must not be
silently labelled regular or implemented by changing playback rate. Persona
appearance/name changes must not change its pinned voice identity.

The extra gate measures audible 10 ms frames rather than counting quiet pauses
as speech. The initial product band is 0.18–0.65 active seconds per authored
syllable (0.22–1.35 for a single syllable). Count authored syllables rather than
characters so non-syllabic erhua remains one sound. These are screening bounds,
not a pronunciation, tone or universal naturalness certificate. Existing clean
audio, full-content, punctuation and ending checks still apply. Keep complete
speaker turns connected; do not concatenate isolated word clips.

WU02 exposed the gap in the older clean-v5 screening bound: the male recording
has 20 syllables in 2.62 active seconds (0.131 seconds/syllable), barely above its
old 0.13 floor, while the owner-preferred female pace has 4.75 active seconds
(0.2375 seconds/syllable). Sentence pauses made their overall durations look less
different. The new gate rejects the rushed male take without slowing the waveform.
The Warm-up retained-file audit is in `artifacts/audio-pace/warmup-audit.json`:
both WU04 narration paragraphs pass for both voices and can retain exact bytes.

Qualify the final decoded MP3; the report binds its PCM, sample rate, exact source
text/pinyin and policy. `verify_pace` remeasures rather than trusting an accepted
flag. The CLI also binds the encoded-file SHA. Generation, cached resume,
composition and delivery must require this supplementary report for new regular
story/turn recordings. Owner selection does not waive the requested regular pace.
Old clean-v5 receipts and profile/release hashes remain historical and unchanged;
they cannot by themselves claim this additional qualification. A failed retained
clip needs a newly generated replacement, not time stretching or pause padding.

```sh
PYTHONPATH=. "$SYNTHESIS_PYTHON" -m scripts.audio_pipeline.pace --audio /path/to/story.mp3 --text '这是你的吗？' --pinyin 'zhè shì nǐ de ma' --report /tmp/story-pace.json
# --qualification /tmp/story-pace.json rechecks the same delivered file.
```

Before broader production, run a bounded two-voice pilot including natural speech,
rushed speech with long pauses, slow speech, questions and a complete multi-turn
scene. The current Warm-up audit and CLI regressions are bounded calibration
evidence; they do not requalify every historical catalog recording.

Each attempt must terminate normally rather than exhausting its speech-token
budget. Silero VAD and waveform measurements locate boundaries conservatively;
internal pauses are measured, not compressed. Significant unexplained activity
outside speech causes rejection. The finishing pass retains consonant padding,
uses brief fades and bounded speech-level gain with peak headroom. SenseVoice
checks content, allowing orthographic homophones and ASR's unwritten 儿 only for
curricular non-syllabic `r`, never `ér`. Matching characters are not reinterpreted
through a second dictionary. Neutral syllables are compared without imposing a
lexical tone from the ASR spelling (for example, isolated `le` written as 乐). This check cannot certify
acoustic tones or erhua.
An optional pinned Whisper-Pinyin encoder/CTC checker resolves small orthographic
ambiguities when the primary ASR rejects an otherwise acoustically qualified
take. It receives audio only, never the expected transcript. Every decoded
syllable must match the authored reading, with bounded Mandarin tone sandhi,
ordered token evidence and at least 0.98 token confidence. This is an additional
pronunciation check, not a replacement synthesis model or a general permission
to ignore ASR failures. All acoustic gates still apply. Evidence and pinned model
hashes remain in the private receipt; low-confidence cases stay rejected.
Known spelling artifacts have narrow rules: a primary B/P/D/T spelling must
agree with the intended base syllable, leave surrounding Hanzi unchanged, and
have independent full-utterance pinyin evidence. Primary `第一` for an isolated
authored `dī` requires independent evidence of exactly one `di1` syllable. Actual
`dì yī`, `dì di`, and `dì` recordings were tested as negatives. This does not
permit generally dropping words or accepting incomplete speech. The pinyin
model alone is not a replacement for the primary content check: short-clip
diagnostics also found confident vowel errors, so retain these bounded rules.
DNSMOS provides speech/background distortion indicators. Neither
model independently certifies pronunciation. DNSMOS's repeated short-clip scoring
is particularly limited; record scores, thresholds and transcript for diagnosis.
The encoded MP3 is decoded and checked for clipping and boundary discontinuities.
The `mandarin-clean-v5` policy qualifies internal punctuation using unprompted
SenseVoice greedy CTC character observations, with the existing strict
Whisper-Pinyin timing fallback. Every authored character must have a corresponding
observed event, in order; missing content, Latin tokens and repaired sequences are
rejected. These primary-ASR observations locate words; they are not a second
pronunciation or tone check. The normal ASR/strict phonetic pronunciation checks
remain separate. The gate measures the interval and actual quiet frames
at each authored boundary, never credits a pause elsewhere, and fails closed on
missing, stale, low-confidence or invalid timing evidence. Authored pinyin and
the pinned `--phonetic` bundle are required for punctuated content; the production
and pilot commands require that bundle up front.

The initial learner-facing phrasing thresholds are 240 ms between observed
syllables plus 60 ms of quiet for internal sentence endings (`。！？.!?`, ellipses
and line breaks); 180 ms plus 30 ms quiet for semicolons, colons and em dashes;
120 ms for commas; and 80 ms for enumeration commas (`、`). Commas can be expressed
through separation/lengthening without literal silence. Adjacent punctuation
collapses to the strongest boundary. Quotes alone create no pause requirement;
terminal punctuation uses the existing completeness/EOS and clean-ending checks,
not a demand for artificial trailing silence. Non-syllabic 儿 stays attached to its
preceding syllable when locating boundaries. These are calibrated product gates,
not universal Mandarin timing rules or a certificate of question/exclamation
intonation, emotional delivery or lexical tones.

Timing currently supports complete utterances up to 30 seconds. A longer clip
with internal punctuation is unverified and rejected, never silently truncated
or split; extend and pilot the timing model before accepting longer passages.
Candidate finishing records per-boundary measurements and observed timing
evidence. Encoding, resumable-cache recovery and release audit recheck the gate
against the decoded MP3. A stored `accepted` flag alone cannot pass. Rejected
attempts retain compact boundary diagnostics and use the existing 24-attempt
budget; full timing evidence is retained on the accepted receipt. No pause is
inserted, removed or time-stretched by this check.

The character observer discards the four nonacoustic metadata frames and keeps
60 ms greedy CTC events, including repeated labels separated by blanks. It does
not use SenseVoice's builtin forced alignment. The event-peak top-eight posterior
mass must reach 0.90 among spellings compatible with the authored sound; this
accounts for probabilities split between 她/他/它 without accepting a different
sound such as 外面 for 外边. These posterior values are model diagnostics, not
accuracy percentages. The primary transcript must still cover the whole text.
Timing v3 retains the raw tokenizer IDs and validates their uniqueness, since
distinct SentencePiece IDs can decode to the same surface text. Each raw
posterior is counted once; matching decoded strings must not invalidate an
otherwise confident observation. Total mass, descending probabilities and the
greedy event's ID, text and peak probability are still checked.
Written non-syllabic 儿 events are grouped with the preceding observed character;
missing events are never invented. Timing v2 permits a weak observed suffix to
share the preceding reliable base only within one acoustically continuous
syllable: the final samples must contain no intervening qualifying 30 ms quiet
span. Those quiet spans use the ending gate's pinned 5 ms frames and thresholds
and are recomputed from the waveform during validation. The complete observed
suffix span is retained. Standalone ér still needs its own confidence; omitted
written suffixes do not acquire invented timing. Other uncertain observations
use the unchanged strict phonetic fallback or reject the candidate.

New character timing carries the SHA-256 of canonical decoded float32 samples
(libsndfile, then scipy resampling to 16 kHz). Encoding, resume and publication
must use that same decoder, and the final MP3 gets fresh observations. Equal
length alone cannot establish that evidence belongs to a recording. Existing
qualified phonetic timing, exact character-timing v1/v2 evidence and MP3s remain
reusable without regeneration.

Calibration is retained in `artifacts/audio-endings/character-timing-controls.json`
and `character-timing-reproduction-pilot.json`: the original noisy female clip
still rejects, the other five workshop clips pass, and four of six deterministic
reproductions previously rejected solely by phonetic timing now pass. Two remain
rejected for uncertain 儿/边 observations. Missing/extra/reordered/wrong/Latin
content, low confidence and same-duration changed samples reject. This is a
versioned timing-method correction, not a reset of exhausted unchanged retries.
Actual-waveform controls in `character-timing-waveform-controls.json` also reject
omitted and repeated speech. Adding 500 ms leading silence shifts observed starts
by 420–540 ms; this bounds model-grid behavior, not true acoustic onsets. The
replacement pilot uses natural instruction text without forced slow speech.

The bounded v2 calibration is in `rhotic-timing-pilot.json` and
`rhotic-timing-controls.json`. A previously rejected 有点儿 paragraph now passes
both raw and delivery-MP3 checks with the observed suffix's 0.751 peak; two other
sampled takes still reject. All six original workshop controls retain the same
five-clean/one-noisy result. The 36 focused regressions include detached noise
after a rhotic base, forged continuity, standalone ér, complete content and exact
v1 reuse. This corrects an orthographic-event versus syllable-confidence mismatch;
it is not permission to ignore a missing syllable or reset unchanged retries.

`token-id-timing-pilot.json` reproduces a concrete v2 false rejection: two
different low-probability token IDs both decode as `be` beside a confident 比.
The female paragraph passes generation and final-MP3 checks after the ID-aware
correction. The same correction fixes the male timing representation while its
actual noise/pause failures still reject it. Three sampled omitted-suffix takes
also continue to reject. All six original real-waveform controls retain their
five-clean/one-noisy result (`token-id-timing-controls.json`). All 40 focused
audio regressions pass, including malformed or repeated IDs, forged probability
mass, exact legacy reuse and the existing waveform/content/noise checks.

The complete release audit decodes each delivered file again and verifies its
sample rate, channels, duration, integrity and recorded quality thresholds.

The `mandarin-clean-v4` policy also checks for detached noise after sentence
endings. The `mandarin-clean-endings-v1` gate uses the existing independent
phonetic observations to locate sentence gaps and the last spoken syllable,
then waits for 30 ms of measured quiet before looking for renewed audible
activity. It rejects isolated clicks, buzzes and breath bursts in that quiet
region. Five-millisecond RMS/peak measurements scale to the recording's speech
level and retain absolute floors. It protects the next sentence's onset and
allows normal vowel/nasal decay beyond a recognizer token's end; token ends
are never used as audio cut points. This is an artifact detector, not a claim
that every possible unwanted sound can be identified automatically.
Short gaps can be entirely covered by the speech guards, and connected sounds
without the required quiet remain outside this detector's scope. Detached bursts
that continue up to a measurement boundary are still assessed within the safe
region, including the final partial frame at EOF.

Qualification checks both internal sentence pauses and the end of the file.
The generated waveform, decoded final MP3, resume path and release audit all
apply this gate. Missing/stale timing or a saved `accepted` flag cannot bypass
it. Each failed candidate uses the same bounded 24-attempt budget; do not mute
pauses, concatenate clips, crop final syllables or weaken thresholds to pass.
Retain compact measurements for rejected attempts and full timing evidence for
accepted MP3s. The representative pilot is in `artifacts/audio-endings/`: the
reported female story paragraph contains detached bursts after two sentences,
while the other five original story recordings retain clean-ending approval.

When rechecking existing audio, decode the retained final MP3 and recompute
qualification without trimming/gain-processing it again. Keep the original
generation recipe and timestamp; record any later qualification explicitly.
Reuse the bytes when they pass. Replace only failed recordings, then rebuild
their measured word alignment and content-hashed references. New production
profiles pin this gate and its implementation hash before bulk generation.

Raw/finished lossless waveforms are TEMPORARY working data only. Delete them when
the attempt finishes. Retain only accepted MP3s, provenance/quality JSON, and
compact failure reports. Do not upload lossless originals to S3. A temporary
pilot may use WAV internally; export MP3 comparisons and remove those WAVs when
calibration is complete.

## Commands

Use the isolated pinned CosyVoice environment, plus
`scripts/audio_pipeline/requirements.txt`; never install these in the API image.
Set `SYNTHESIS_PYTHON` to that environment's Python. Reference/model paths and AWS
credentials stay outside committed application configuration.

```sh
make learning-audio-prepare-v2 ARGS='--directory /path/to/audio-tools'
make learning-audio-pilot ARGS='--source /path/to/CosyVoice --weights /path/to/weights --asr /path/to/audio-tools/asr --phonetic /path/to/audio-tools/phonetic --voice female --reference /path/to/reference.wav --output /tmp/audio-pilot'
make learning-audio-produce ARGS='--source /path/to/CosyVoice --weights /path/to/weights --asr /path/to/audio-tools/asr --phonetic /path/to/audio-tools/phonetic --voice female --reference /path/to/reference.wav'
# Repeat generation with --voice male and its matching reference.
make learning-audio-release ARGS='--output /tmp/audio-release --activate'
```

`--ids` accepts an explicit subset for calibration. `--limit` bounds a run.
Offline punctuation CLI regressions run with `make learning-audio-test
SYNTHESIS_PYTHON=/path/to/audio-environment/bin/python`. They exercise supplied
waveforms and recognizer output through the public check command, including
rushed/misplaced breaks, missing evidence, stale policies, quotes and erhua.
The v3 qualification pilot rechecks 26 representative existing female/male MP3s
(isolated words, statements, questions, commas, quotations and stories), plus
earlier full-story pilot takes. Reports are in `artifacts/audio-punctuation/`.
This is requalification of existing audio, not a claim that rejected old files
have been regenerated or that any release was activated. Both voice recipes get
new policy/code hashes; existing release metadata and exact-file human exceptions
remain immutable. Bulk synthesis still requires the representative voice pilot.

`--shards N --shard I` partitions finite jobs deterministically. Rerunning resumes
completed uploads and repairs interruptions from checksum-verified local MP3/JSON
receipts in ignored `.audio-production/`. Failed attempts do not publish audio.
Each item has at most twenty-four attempts across zero-shot, natural instruction,
concise clear-reading instruction and the same model's unprompted text mode.
The latter retains the same adult speaker's acoustic conditioning; it does not
change the spoken language or select another voice. The recipe records the
bounded mode/variant sequence. Seeds advance on every attempt. Isolated default readings can also
try native Hanzi spelling; neutral syllables can try romanized input. Every variant
passes the same acceptance checks. Native spelling never replaces an explicitly
selected nondefault reading. Failed attempts persist across resumptions, including
normal EOS with too few tokens for the vocoder. Unrelated synthesis/GPU failures
are reported as errors rather than silently accepted or retried as speech.
A failed batch exits nonzero.

Before scheduling a persistently failing utterance again, compare the actual
model input, seed, synthesis mode, instruction prompt, speaker/reference and
profile with retained attempts. A variant named `phonemes` does not establish
that phonemes were injected: ordinary connected text with no authored lexical
controls can remain native Hanzi. Changing a content ID or correcting pinyin
metadata must not regenerate the same input/seed/mode/instruction/profile and
recreate identical MP3s. Preserve original attempt counts, failed metrics and
candidate hashes; any bounded new input experiment must document the actual
input difference and retain its cumulative budget.

An unchanged model input does not prove that retained speech has the corrected
reading. Reuse across changed pinyin requires a separate full new-reading
qualification, bound to the original MP3 bytes, profile and receipt, with fresh
independent acoustic evidence for the intended syllables/tones and complete
measured timing where required. Source pinyin, Hanzi transcripts and passing pace
are not pronunciation evidence. Keep the original receipt ID, original strict or
owner-selected status, and failed checks; never retag a receipt or weaken exact
reading alias checks. Missing or failing evidence remains blocked, and partial
rechecks cannot automatically promote or publish audio.

Silent quotation marks may be added around an isolated target or removed around
an authored quoted target. These variants preserve every spoken syllable and its
selected reading. Keep the actual input, mode and instructions in each receipt;
an unprompted take must not claim it received a clear-reading instruction.
Status files report processed, accepted, failed, elapsed and estimated time.

The release command downloads and verifies EVERY MP3 against its metadata and
quality outcome, requires BOTH voices for EVERY content ID, and prepares 256
bounded metadata shards. Activation changes the local `active.json` pointer only
after the complete snapshot exists. Restart the local API to adopt it. Production
still uses the repository's separate manual release gates; validation never
deploys the app. The API keeps no model dependencies and performs no S3 reads.

On 2026-10-15 the project owner explicitly approved the female and male review
MP3s of `你会说“乒”吗？`. These are exact-file exceptions to pronunciation
verification only, recorded in `app/data/sentence-audio/manual-approvals.json`.
Each approval binds the content, reading, voice, recipe and MP3 SHA-256. Preserve
the failed automated result in its receipt and mark it `human-approved`; never
relabel it as an automated pass. The release reports automated and human-approved
counts separately. All acoustic, encoding, EOS, integrity and completeness gates
still apply. This authorization does not approve other takes or introduce a
routine human review gate for future generation.

On 2026-10-22 the owner explicitly authorized the current course rollout to
finish with the best available take after ten failed attempts. This scoped
exception uses `generate-workshop-audio.py --owner-best-available-after 10`.
It does not change a voice profile, the strict automatic policy, or the separate
full sentence-release publisher. Normal automatic passes still stop immediately.
During this bounded pass, retain up to ten candidate MP3s temporarily, measure
the final delivery audio, and prefer complete content with usable observed word
timing, then clean endings/phrasing and speech quality. Capped, abruptly cut off,
silent, corrupt, clipped, or otherwise unplayable speech cannot be selected.
When earlier failed files were already deleted, preserve that history and record
the new ten-attempt retention pass explicitly; never reset its resumable budget.

The selected receipt is `owner-authorized-best-available`, with
`quality.accepted: false` and every failed automatic check retained. Its
`ownerSelection` binds the authorization, all ten attempts, deterministic
candidate comparison, exact text/reading/voice/profile, MP3 SHA-256, and measured
quality. Offline course verification uses the separate `owner_selection.py`
verifier; it must not call this an automated pass or a human listening review.
Report owner-selected counts separately. Delete unselected temporary MP3s once
the selected bytes and provenance are safely retained. Keep no lossless audio.
For story/turn production requiring word timings, selection must first exclude
takes without complete measured alignment under the existing primary or narrow
independent-sandhi rules. Keep its candidate MP3s and eligibility evidence until
the separate packager verifies the delivered alignment. A missing-candidate
recovery pass preserves all earlier attempts, records its nonzero retention
start explicitly, and has one further ten-attempt limit; exhaustion does not
automatically authorize another pass.

The v1 verifier supports two explicitly reviewed historical source hashes,
`e5a5aa57381a44a766392ad269dee40d1f84efc490a1db90f072d7d9fe61680f` and
`2a049bfbfdb753ed48259cfab34ab4683912c570292778fb430fa12dd25f0680`.
Their exact source snapshots differ only by two blank lines in the verifier's
imports; their syntax trees and selection semantics are identical. Snapshots and
the diff are retained in the frontend's
`artifacts/learning/full-course-audio/owner-verifier-compatibility/` evidence.
Receipts keep their original hashes. Compatibility still requires every current
binding, ten-attempt, candidate-ranking, byte, waveform and completeness check;
it does not accept an unknown historical source or waive regular pace.

Selected word highlighting may use low-confidence unprompted final-MP3 CTC
observations with that uncertainty explicitly recorded. A missing *written*
non-syllabic 儿 can share its real base syllable's measured span; this invents no
event, extends no endpoint and makes no rhotic-pronunciation claim. Missing
ordinary syllables never receive fabricated times. The strict timing policy
and all existing automatic recording qualifications remain unchanged.

One primary spelling ambiguity can be resolved during owner-selected story
packaging by `owner_sandhi_alignment.py`: an observed third-tone spelling before
another third tone can represent the authored second-plus-third surface (for
example, 友好 versus 游好). A separate exact policy covers ordinary 一 before a
fourth tone when the observed spelling represents its `yi2` surface (such as
遗忘 for 一忘). It conservatively excludes ordinals, digit sequences,
compound-number endings, weekdays and calendar dates. Both policies require
fresh pinned phonetic observations of
the exact delivered samples, at least 0.98 confidence for both bases and tones,
and matching local event times. Neither can cross authored punctuation or an
incomplete secondary observation; unrelated incomplete observations remain
explicit barriers. The
supplement binds both observers, MP3 and PCM; the portable course build verifies
the actual independent events as well as their hashes. Original primary events,
timestamps, audio and failed receipt metrics stay unchanged. This is alignment
evidence for an authorized selection, never automatic qualification.

Existing sentence IDs already distinguish text and pronunciation. Recipe hashes
namespace recordings, while an immutable release selects the two variants. The
API returns the default reference plus available voice references. Browser cache
keys include voice ID; downloading remains on demand. Character readings can
reuse identical isolated-word assets instead of creating duplicate recordings.

## Retirement

Only retire a namespace after its replacement is complete AND all serving
environments use the replacement release. Check the dry-run inventory first:

```sh
make learning-audio-cleanup ARGS='--retired-version OLD_HASH --served-release ACTIVE_RELEASE'
# Add --apply to delete exactly this retired namespace, including old versions
# and delete markers. Active voice namespaces and license files are protected.
```

Do not delete a namespace still referenced by a running older API release.
Invalidate only retired CDN paths if immediate removal is needed; no blanket
bucket deletion. MP3s are immutable; a replacement always receives a new version.

## Future stories

Use the same language/voice profiles for complete natural passages. Content IDs
identify a passage and its reading; a story manifest can order those IDs without
changing the audio contract. Validate a short multi-sentence passage in the pilot.
Do not silently split at arbitrary character counts or combine isolated words.
The current delivery limit is 2 MB per clip and 90 seconds of source audio;
future story authoring should supply bounded passages. Story playback and its
buffering UI are outside this change.

Story playback must support highlighting the word currently being spoken. Add
word timing metadata by grouping unprompted character/syllable observations from
the finished, decoded delivery MP3, after trimming, fades and encoding. Never
force-align the expected text as evidence that it was spoken correctly. Existing MP3s
can be aligned without synthesizing them again or retaining lossless originals.
Do not estimate timestamps from character counts or reuse another voice's timing.

Store an optional immutable alignment JSON alongside each voice's audio, keyed
by the audio SHA-256 and a version of the authored tokenization/alignment recipe.
Each spoken token needs its stable story token ID and start/end seconds, relative
to that passage's playback timeline. Mandarin compound words follow the lesson's
authored vocabulary segmentation, not one highlight per Hanzi. Keep punctuation
and silent spans separate; validate token coverage and ordered in-range times
before publishing an alignment.

The future API can expose a small alignment reference with the audio reference;
fetch it on demand for the selected passage and voice. The frontend must use the
audio player's current playback position for highlights, including pause, seek,
replay and rate changes, rather than advancing an independent timer. Missing or
invalid alignment must leave audio playback usable without guessed highlights.
This is a future story requirement, not a gate or additional generation step for
the current audio batch.

## Evaluation provenance

- CosyVoice: pinned revisions in each voice profile; https://github.com/FunAudioLLM/CosyVoice
- Reference recordings: AISHELL-3, recorded reference only; source, transcript,
  speaker identity and license are pinned per profile. Targets are generated.
- SenseVoiceSmall: https://huggingface.co/FunAudioLLM/SenseVoiceSmall,
  revision `3847d57b6bdf2dd8875cb1508d2af43d80a16bf7`; offline content checks.
- Silero VAD 6.2.0: https://github.com/snakers4/silero-vad (MIT).
- Microsoft DNS-Challenge DNSMOS, revision
  `591184a9fcb2cbdec02520fed81a32bbbf9d73ff`, CC BY 4.0:
  https://github.com/microsoft/DNS-Challenge/tree/591184a9fcb2cbdec02520fed81a32bbbf9d73ff/DNSMOS
  The offline scoring implementation adapts the published windowing/calibration
  procedure; the model checksum is pinned in the quality policy. Attribution and
  license accompany this production tooling, not the browser runtime.
- Whisper-Pinyin: https://huggingface.co/walston/whisper-pinyin, revision
  `7840ab2afafecd7b03aa66c7517d756c180dbb43`; modified Whisper source at
  https://huggingface.co/spaces/walston/whisper-pinyin-demo, revision
  `eb69fe62c021e46d5bd34d1da17c23cecedea484`. Both repository cards declare MIT.
  Pinned cards and the underlying Whisper MIT notice are retained with the offline
  tooling. Source, checkpoint and tokens have independent SHA-256 checks. The demo
  application is not run, and remote model code is not trusted or executed.

### Live native-speaker listening approvals (2026-11-01)

The review dashboard overlays immutable catalog warnings with PostgreSQL decisions.
A verified account with the shared admin role (`ADMIN.Emails` or `ADMIN_EMAILS`)
may approve or undo approval after native-speaker listening. The same role gates
all dashboard screens, direct prototype URLs and local jump-ahead requests.
The former audio-review-only allowlist no longer grants access. Every write requires
normal session, origin and CSRF protection. No reviewer permission is implied by
being signed in, being in development preview, or knowing a recording ID.

Deploy the API/UI and migration 0032 once; subsequent approvals commit immediately
without publishing MP3s or deploying. Decisions persist across application restarts
and releases. They bind exact voice profile, MP3 SHA, displayed text and pinyin;
new recordings/identities/reading contracts require new review. Export the review
registry alongside the active catalog with `scripts/export-final-audio-review.py
--active` in the frontend repository. The active registry must match the active
release; a mismatch fails closed. Keep previous registries and review audit events.

The effective dashboard warning disappears on approval and the item remains in
Approved. Undo restores its original warning. Original automated failures and
owner-selection receipts are never rewritten as automated passes. Listening
approval does not create missing word timings, alter pace, grant learning progress
or turn failed alignment into usable alignment. The live metadata endpoint is
uncached; other open dashboard tabs refresh on focus and every 30 seconds. Reviewer
identities are retained in private audit records, not public review metadata.
