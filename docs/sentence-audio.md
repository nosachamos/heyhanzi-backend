# Sentence pronunciation

The original recipe documented below is the legacy catalogue. New production
uses the two-voice automated pipeline in [audio-production.md](audio-production.md).
Do not regenerate the forced-slow legacy recipe.

Sentence recordings are generated once, before application release. There are
no synthesis workers, queues, model dependencies, or AWS reads in the API request
process. Pressing a speaker downloads an existing recording directly from the
audio CDN. The authenticated notebook response already includes its URL, SHA-256
version, MIME type and byte length, so playback needs no resolution request.

The current catalogue contains 5,949 distinct authored/example utterances plus
14,630 word-reading practices (the two existing fallback forms for 7,315 lexical
readings). It does not invent new lessons or change learner progress. Identical
sentence text and normalized pinyin share an asset across cards. Sentence IDs
include the language, text and reading; a different pronunciation has a different
ID. Each voice profile records a pinned recipe, whose hash forms its storage
namespace. Uploads use `If-None-Match: *` and never overwrite a published take.

## Voice and processing

New recordings use the adult female and male profiles in
`app/data/sentence-audio/voices/`. See [audio-production.md](audio-production.md)
for their pinned settings, bounded retries, automated qualification and release
commands. Targets are fully generated with CosyVoice; short reference recordings
condition the voice. Speech is never assembled from prerecorded characters.

`voice.json` and `published/<voice-hash>/` retain the legacy catalogue's provenance
until its verified replacement is activated. The legacy generator is retired;
its Make entry point now invokes the qualified pipeline. Do not copy its old
forced-slow instruction, tempo adjustment or gain into new production.

## Storage and delivery

The dedicated `heyhanzi-learning-audio-642022872145` S3 bucket blocks public
access, requires TLS, uses server-side encryption and versioning. Its dedicated
CloudFront distribution uses Origin Access Control and serves only MP3s under
`sentences/`; provenance JSON is not available through the CDN. It is separate
from the website distribution and does not publish application code.
The same CDN serves the dataset attribution and license copies; media responses
link to them using the HTTP `Link` header.

Pronunciation clips are public, immutable educational media. Account information,
learner progress and selected vocabulary stay in authenticated, uncached API
responses. The CDN cache does not receive cookies, authorization, query strings
or learner preferences. Its CORS response policy permits the production website
and local HTTPS origin. The frontend CSP permits the exact audio CDN hostname.
Application credentials are not needed for audio delivery.

The API reads compact metadata shards with a bounded 16-shard cache. It never
loads every MP3 or queries S3 for a card. Published sentence references are
language-neutral `audio-reference-v1` objects with a `url`. The browser fetches
only on a speaker click, omits credentials/referrers, bounds download size,
checks the SHA-256 and caches at most 32 settled resources / 8 MB. It cancels
downloads on logout and bounds concurrent sentence downloads to four. A notebook
shares one lazily opened AudioContext, releases evicted decoded buffers, and
plays one clip at a time. Card changes and dismissal stop playback.

## Content releases

Use the finite generation and release commands in
[audio-production.md](audio-production.md). The pipeline requires both voices for
all catalogue entries, then downloads and validates every compressed recording
before preparing immutable metadata in `releases/<release-id>/`. An atomic
`active.json` pointer activates the complete local snapshot. Restart the local
API to adopt it. Application deployment remains a separate manual operation.

A failed or incomplete batch never replaces the serving catalogue. Resumable
receipts repair interrupted uploads without overwriting immutable media. New
examples or readings require extending the catalogue, generating both variants,
and passing the complete release audit. Only retire older S3 namespaces once
all serving environments have adopted the replacement. Cleanup includes old S3
object versions and leaves active media and license files intact.
