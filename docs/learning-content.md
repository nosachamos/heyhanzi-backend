# Mandarin vocabulary and examples

Current lexical spelling corrections are recorded in
`app/data/learning/vocabulary-corrections.json`. Offline example and audio
builders apply this overlay to historical source records; never rewrite applied
migration data. Migration `0023_online_reading` corrects 线上 from `xiàshàng` to
`xiànshàng` in place, preserving its vocabulary ID, meaning and learner exposures.
The current public vocabulary download uses the corrected reading. Historical
audio receipts remain unchanged; newly generated audio must use the corrected
text/reading identity.

Migration `0024_example_vocabulary` adds the genuine compound 伙食 (`huǒshí`,
“food; meals”) to the 伙 notebook card, sequence 1267, after 食 at sequence 924.
The same forward migration adds 松树 (`sōngshù`, “pine tree”) on 松 at 1028
and 酷热 (`kùrè`, “extremely hot; scorching heat”) on 酷 at 2022, both after
their constituent characters. Their explicit displays and acknowledgements
remain separate from character visits.
The migration also repairs 大都 “for the most part” to `dàdōu` and 卷子 “test paper”
to `juànzi` in place. Their old IDs, meanings and exposure records remain intact;
the historical capital and steamed-roll readings are not these lexical senses.
Frozen migration receipts and current public/correction views agree; historical
recordings retain their original provenance and are not silently relabelled.

The character curriculum remains the pinned HSK 2025 edition, warm-up followed
by HSK 1–6. Character IDs, ordering, enrolments and completion history are
unchanged. Supplementary vocabulary has **no official HSK word-level claim**;
its constituents determine when it can be introduced.

Migration `0016_practical_vocabulary` adds sentence prerequisites and a curated
practical supplement covering travel, payments, deliveries, digital services,
healthcare, housing, work and everyday communication. This is a finite teaching
catalogue, not an import of the entire CC-CEDICT dictionary. Readings and existing
exposure references are retained; the migration is additive and idempotent.

## Sentence source

The source is [Zhongdex by SayMei](https://github.com/saymei/zhongdex), specifically
`data/sentences.jsonl` at revision
`02d75a8974683460ffcfa4e0d2673bc8113ceb67`, SHA-256
`32e7ace25a6b66aade35391233d0f5ba5838d02662892e02e995031a0e42f776`.
Its **data** licence is CC BY-SA 4.0; its code licence is separate. We import
sentence text, English translations, contextual readings and source IDs only.
We do not import its HSK assignments, audio, software or dictionary canon.
The original per-sentence source metadata is retained under `source.upstream`.

The builder excludes the upstream single-character-headword pool, Latin/numeric
prose, missing/misaligned readings, unknown dictionary tokens and sentences with
characters unavailable at the introduction point. It selects short examples,
then adds explicitly tokenized editorial examples and short dialogue adaptations
where needed. Dictionary-derived annotations use the already pinned CC-CEDICT
archive. A small number of contemporary multiword practical phrases are editorial
combinations with explicitly specified readings.

The 1,939 learning steps retain genuinely contextual annotated examples, with
short utterances in the first very small warm-up inventories. Quoted-word frames
such as “I am learning [word]”, “Can you say [word]?” and “This is [word]” have
been retired. Some bound-form introductions have fewer than three usable examples,
or none until a compound is introduced. Later editorial alternatives are shown
only when their actual words become available; character familiarity does not
grant vocabulary exposure. `app/data/learning/examples-audit.json` records these
introduction-coverage gaps rather than claiming universal three-example coverage.

**Content review status:** prerequisite, segmentation and alignment validation
is automated; selected/editorial examples have been spot-checked. This is not a
claim that the entire imported corpus has had native-speaker proofreading.
Before describing the programme as a finished fluency curriculum, native-speaker
review and actual grammar/listening/speaking/reading lessons are still needed.
The upstream corpus itself reports quality limitations. Corrections belong in
our editorial inputs rather than silent run-time sentence generation.

## On-demand API contract

`GET /learning/tracks/{track}/blocks/{block}/vocabulary` returns `examples` (up
to three, exactly three for a normal introduction with the default first page),
while `example` retains the first sentence for older clients. Vocabulary remains
paged (20 by default, at most 40). Very small custom pages may temporarily have
fewer safe examples because their prerequisites have not been displayed.

Each sentence includes Chinese text, pinyin, English, provenance and ordered
word/punctuation tokens. Tokens include contextual pinyin, a concise meaning,
English character ranges when a clear lexical correspondence exists, and the
current-character/whole-word highlight. We do not guess alignments for idioms
or implicit English grammar. Those tokens use the parenthetical meaning line.

The planner prioritizes prerequisites for available character examples, then a
bounded set of related word examples. Required compounds must be on the currently
rendered page or have explicit exposure history; known characters alone never
count as taught words. GET does not write exposure. The existing rendered-list
acknowledgement remains the only exposure boundary, with advancement waiting for
successful acknowledgements. Cursor ordering does not depend on acknowledgement
history.

Sentence files are keyed by trusted index entries in
`app/data/learning/examples/`. Workers read only requested files, with an LRU
cache capped at 128 files. They do not import the corpus at startup or send it
with path windows. The frontend receives sentences with the selected notebook
card, with keyboard, hover and touch support. Tooltip geometry is per sentence:
all possible fallback meanings reserve a shared grid cell, so changing the
hovered token never changes the tooltip's width or height. Fade preserves size.

## Offline authoring

Download the pinned sentence file and the dictionary matching the SHA recorded
in `alembic/data/0012_sources/sources.json`, then run from this repository:

```sh
python scripts/curriculum/build_examples.py \
  --examples-only \
  --sentences /path/to/pinned/sentences.jsonl \
  --dictionary /path/to/pinned/cedict.txt.gz
```

The builder reconstructs the preceding catalogue from frozen migration inputs;
it does not use the frontend's current download or fetch anything at runtime.
Editorial inputs are `scripts/curriculum/mandarin-editorial.tsv` and
`mandarin-practical-vocabulary.tsv`. After building examples, run
`python scripts/curriculum/refine_annotations.py --vocabulary
/path/to/published/mandarin-hsk2025.json` (on one line). This applies the explicit
sentence fixes in `mandarin-corrections.tsv`, corrects problematic contextual
readings/grammar boundaries and improves English word alignment. Our corrections
retain upstream IDs and mark `source.editorialCorrection`. The initial 40 source
corrections and all token meanings were checked independently of the migration
seed; they do not claim exhaustive proofreading. The audit includes coverage by level and
all remaining character-practice introductions.

The builder refuses to overwrite existing migration data with different content.
`--examples-only` leaves migration data untouched; new vocabulary requires an
explicit new `--vocabulary-output` and matching forward migration.

Once migration 0016 is applied, its seed is frozen. Future vocabulary changes
must use a new forward migration; do not overwrite applied migration data.
Sentence metadata can be corrected independently. Publish the matching adapted
vocabulary and sentence downloads in the frontend's `public/curriculum-data/`
and update its `MANDARIN-DATA-NOTICE.txt`. Those public downloads contain no
learner data and are never automatically fetched by the learning application.

## Common-word examples and lexical identity

Common words use the separately authored `app/data/learning/common-word-examples/`
index and shards. The index maps vocabulary IDs to `text`, `reading_key`,
`sense_key` and a trusted shard filename; each shard maps those IDs to annotated
sentence arrays. Runtime selection verifies the complete identity, including
pinyin tones (spaces, apostrophes and formatting hyphens do not distinguish readings). A different
sense or pronunciation cannot borrow an example merely because its Hanzi match.
The index is cached once and at most 32 shards are retained in memory.

Tokens may pin a `vocabularyId`; these require that exact lexical entry to have
been displayed on the current page or previously acknowledged. Character-card
completion alone cannot unlock such a lexical sense. The existing notebook
support-word planner lists eligible sentence dependencies before using them.
Unknown characters and future lexical introductions remain unavailable. If no
authored sentence satisfies these prerequisites, the example remains absent;
there is no generated "I am learning [word]" or bare-word sentence fallback.

A bound form may have an explicitly reviewed `targetSpan` inside a known whole
compound token. The span pins the target vocabulary ID, reading and character
range; runtime validates those against the requested identity. It retains the
whole compound's own exposure dependency and also requires the target lexical
entry to be visible or seen. It never splits an unintroduced compound into
familiar characters to manufacture eligibility.

Reviewed default-gloss corrections are ID scoped in
`app/data/learning/common-word-meaning-corrections-review-{a,b,c}.json`.
`word_translations` is shared by the compiler and notebook API. A correction
applies only when its `old` value exactly matches the current learner-facing
English definition. Another ID, a changed definition or a nonempty `sense_key`
keeps its own meaning. Corrections include review reasons, retain the original
source datasets and do not write learner exposure or completion.

The compiler also emits `common-word-primary-meaning-corrections.json` to bridge
reviewed default IDs to primary character cards and core tokens. The shared
`primary_translations` requires the same written form, tone-preserving reading
and old definition, and verifies the bridge against the current ID review.
It therefore corrects the classifier reading 只 zhī without changing 只 zhǐ
or any separately introduced course sense. Stored character-example tokens use
this same boundary only when they have no explicit lexical ID.
