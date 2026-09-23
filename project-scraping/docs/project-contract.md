# Project data contract

This file and `scrapectl/project_contract.py` are **project-owned extension
points**. Replace the canvas examples with the product's real scope before
assigning source spiders. Do not put domain rules into the universal agent skill.

## Default canvas contract

One record is one explicitly identified source entity or fact. Every record has
`source_key` and at least a name or absolute HTTP(S) URL. Other published fields
are named features carrying `{value: string, unit: string|null}`. Missing optional
information stays missing. URLs identify evidence or detail pages, not a universal
record identity: several independently keyed facts may cite the same PDF.

The spider chooses a native ID or a documented natural/composite key with
`scrapectl.identity.source_key`. The shared pipeline already hashes the source ID
and explicit key. It does not deduplicate by URL. Keys must not depend on mutable
names, row positions or document bytes unless that identity choice is explicitly
part of this product's contract. No automatic fallback or fuzzy matching.

## Specify for each product

Describe the record unit and any record kinds; required and optional fields;
units, currency and reporting-period rules; allowed source omissions; evidence
location; identity limitations; and the complete scope of a run. Give at least
one accepted example and one plausible but wrong interpretation. Reference a
small relevant fixture and the source's completion condition.

For example, an authorization, a program amendment and executed purchases may
need different identities even when they appear in one report. That example is
not a buyback schema imposed on every generated project.

Put deterministic domain validation in `validate_payload(payload)` in
`scrapectl/project_contract.py`. Raise `ValueError` with a useful reason. The
normalization/staging path calls this hook before accepting output. Keep it pure
and non-mutating. Change `PROCESSING_VERSION` when a customization changes
output semantics. More domain columns require deliberate changes to `models.py`,
`RECORD_FIELDS`, the loader/item and API/frontend/tests; the generator only
renames nouns, it does not invent a domain schema.

## Publication semantics (current, intentional defaults)

The worker requires complete execution, nonzero items, matching retained/staged
counts, unique explicit keys and no processing errors. Publication upserts known
keys and refuses to overwrite newer observations with older input. It does not
retire records that disappeared. For an observed record, features absent from its
new observation are removed. A failed run does not publish its valid subset.

This is not automatic snapshot replacement, an event stream, or an append-only
history. A generic record-count decrease is not automatically a parser failure;
source-specific completeness tests must distinguish legitimate changes. A
reviewed total or explicit exhaustion condition is stronger than an estimated
baseline. Supporting genuinely empty successful scopes or incremental event
publication requires a separate, explicit project policy and tests; do not
weaken the default worker guard to make a source pass.
