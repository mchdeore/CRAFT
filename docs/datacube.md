# Engineering datacube

The application programming interface (API) uses Hypertext Transfer Protocol (HTTP) and JavaScript Object Notation (JSON). Tool calls use the Model Context Protocol (MCP). Identifiers are universally unique identifiers (UUIDs), and times use Coordinated Universal Time (UTC). Content hashes use the 256-bit Secure Hash Algorithm (SHA-256). Schema blocks use Structured Query Language (SQL). Spreadsheet files use the Excel format (xlsx) or comma-separated values (CSV); text files use Markdown, Hypertext Markup Language (HTML) or Portable Document Format (PDF).

## Purpose

The engineering datacube answers engineering questions about missions from cited sources. It finds documents and passages, finds missions by their parameters, compares missions and finds similar ones, in chat and in the browser. Every result carries a citation that can be checked against the original.

The datacube runs on labelled simulated data. Every mission, document and value carries the simulated label.

## Parts

| Part | Holds | Owns the truth for | Rebuildable |
|---|---|---|---|
| Document provider | The original files and every version of them | What a document says | No; it is the source |
| Library | Text chunks, summaries, facet texts, vectors and version differences | Nothing; it mirrors the provider | Yes, from the provider |
| Cube | Missions, silos, stages, elements, the parameter dictionary and approved values | What CRAFT has accepted as a mission's parameters | No; approvals are records |

The library and cube never store provider original bytes. The local provider folder stores generated originals and immutable versions; application operations read them when indexing, checking a citation or opening a preview. Document content changes enter through the provider command line, and indexing records the new version and differences.

## Document provider

The provider is a folder on disk with a sidecar record. The setting `CRAFT_PROVIDER_ROOT` names the folder. A document's path says where it belongs.

```text
<provider root>/
  missions/
    msn-sim-01/
      requirements/st2-definition/system-requirements.md
      engineering/st3-design/power-budget.xlsx
  reference/
    glossary.md
  .provider/
    items.jsonl            one line per item: identifier, path, title, labels, versions
    changes.jsonl          the change feed, one numbered line per change
    versions/<item>/<n>    the bytes of every version
```

| Path part | Meaning | If missing or unknown |
|---|---|---|
| `missions/<mission key>` | The mission | The file is reference material, readable by every account |
| `<silo>` | One of the six silos in [Missions, silos and stages](datacube.md#missions-silos-and-stages) | The file belongs to the mission only |
| `<stage>` | One of the stage keys in [Missions, silos and stages](datacube.md#missions-silos-and-stages) | The file has no stage |

| Concept | Rule | Example |
|---|---|---|
| Item | `itm_` and 12 lowercase hexadecimal characters, assigned when the file is first seen. A move or rename keeps it | `itm_4f9c2a0b1c2d` |
| Version | Numbered from 1. Every content change makes a new version. Old versions stay readable; each new version records its original filename and media type | `itm_4f9c2a0b1c2d` version 3 |
| Content hash | SHA-256 of the version's bytes | `sha256:9b1e…` |
| Move | Same item, new path. A file that vanished and a new file with the same hash in one scan are one move | Path changes; item and versions stay |
| Delete | The item is marked deleted. Its versions stay | `deleted_at` is set |
| Labels | `simulated` and `classification` (`unclassified`, `protected-a`, `protected-b`) from `items.jsonl`; new items are simulated and unclassified | |

The provider offers six functions. CRAFT's indexing and preview use the first four. People and demo scripts use the last two through the command line.

| Function | Does | Never does |
|---|---|---|
| `scan()` | Compares the folder with `items.jsonl` and appends added, changed, moved and deleted items to the change feed | Change a document |
| `list_changes(cursor)` | Returns up to 500 changes after the cursor and the new cursor | Return file contents |
| `get_item(item)` | Returns path, title, labels, the deleted flag and the version list | Return another item |
| `read_content(item, version)` | Returns the bytes of that version | Return another version |
| `write_content(path, bytes)` | Writes the file and records a new version | Get called by an operation |
| `delete_item(item)` | Removes the file and marks the item deleted | Erase a version |

The command line wraps them: `craft provider scan`, `craft provider put <path> <file>`, `craft provider rm <path>` and `craft provider log`.

## Library: chunks, summaries and citations

| Kept | Why | Not kept |
|---|---|---|
| Item, version, hash, path, tags and labels | Stable citations and moved files | Original bytes |
| Chunks: ordered text and table blocks with locators | Verbatim quotes, keyword and vector search | Page images |
| One summary per document version | Fast scanning before reading chunks | Model reasoning |
| Facet texts per mission ([Facets](datacube.md#facets)) | Similar missions | Uncited facet text |
| One vector per chunk, document version and facet text | Semantic search | A vector without its model and recipe |
| Block differences between versions | What changed | A full copy of each version |

One converter turns each allowed format into chunks. A format not listed is refused with a reason. Without OCR opt-in, a PDF without a text layer is refused with the reason `no text layer`. With `--enrich-ocr`, a scanned PDF or image without convertible text can instead have an OCR-only version record, with no chunks or embeddings ([Missions, silos and stages](datacube.md#missions-silos-and-stages)).

| Format | One chunk is | Locator |
|---|---|---|
| Markdown, plain text, HTML | The paragraphs under one heading, split at about 300 words. Scripts are removed from HTML | `heading=2.3;block=4` |
| Word (docx) | The paragraphs under one heading, split at about 300 words | `heading=2.3;para=4` |
| PDF with a text layer | The text of one page, split at about 300 words | `page=4;block=2` |
| PowerPoint (pptx) | One slide with its speaker notes | `slide=7` |
| Excel (xlsx), CSV | One table region per sheet: its header row and up to 30 rows. Literal cell values only; formulas, macros and links never run | `sheet=Power;range=A4:D9` |
| A table in any other format | Its header row and up to 30 rows; the header repeats when the table is split | the format's locator plus `;rows=1-30` |

Tables use pipe-delimited rows with repeated headers. Cell line breaks become spaces; literal pipes and backslashes are escaped. A spreadsheet range names the source table region, and `rows` identifies its data-row window.

Each chunk keeps three text fields: `text` (the words exactly as in the source), `heading_path` (the headings above it, such as `2 Power > 2.3 Margins`, or the slide title, or the sheet name) and `locator`.

Each document version has one summary of at most 150 words, made by one model call and labelled `generated`. The summary says what the document is and which mission, subsystem, review or decision it concerns, and gives its key requirements, findings, figures with units and open issues. Indexing summarizes and embeds up to eight documents at once and writes them in source order. Optional OCR enrichment is queued and stores plain extracted text on the document version; it does not change ranking or embedding inputs.

A citation handle names everything needed to show and re-check a quote. It survives a rebuild of the library; chunk identifiers do not.

```json
{"item": "itm_4f9c2a0b1c2d", "version": 3, "block": 12, "locator": "page=4;block=2",
 "quoteSha256": "c41d…", "simulated": true}
```

`block` is the chunk's position in its document version, from 1. `quoteSha256` is the SHA-256 of the quoted text. A value's citation adds `;row=N` to the locator and hashes that row's text.

## Tables, tags and embeddings

Tags filter; embeddings rank. Mission, silo and stage tags come from the provider path and are copied to each document version and chunk. Tags are never embedded.

![Datacube tables, their relations and what is embedded](images/datacube-tables.svg)

Thick blue arrows go into `vectors`: chunks, document versions and facet texts are the only embedded records. Access grants ([Access](datacube.md#access)) are not shown.

| Record | Tags | Embeddings | What is embedded |
|---|---|---|---|
| Chunk | Mission, silo, stage, simulated, from its document | 1 | `chunk-v1`: title, heading path and text |
| Document version | Mission, silo, stage from the path; simulated and classification from the provider | 1 | `doc-v1`: title and summary (stored OCR text is not embedded) |
| Facet text | Mission and facet | 1 | `facet-v1`: the facet text |
| Mission | Simulated; current stage | None; its facet texts carry its vectors | |
| Cube value | Mission, silo, stage | None; found by filters and by the chunk it cites | |

Vectors have 1536 dimensions and use cosine distance. The embedding model is the one in [Models and confidence](agent.md#models-and-confidence).

[Database](datacube.md#database) defines the library tables in migration 0006: `provider_items`, `doc_versions`, `chunks`, `vectors`, `version_diffs`, `index_cursor` and `mission_grants`. The cube tables follow.

```sql
-- 0009_create_missions                             craft_app: SELECT (seed and command line write as craft_owner)
create table silos (key text primary key, label text not null, seq int not null unique);
create table stages (key text primary key, label text not null, seq int not null unique,
  gate text not null);
create table missions (key text primary key check (key ~ '^msn-[a-z0-9-]{1,40}$'),
  title text not null, current_stage text not null references stages,
  simulated boolean not null, created_at ts);
create table mission_aliases (alias text primary key check (alias = lower(alias)),
  mission_key text not null references missions);
create table elements (id, mission_key text not null references missions,
  silo text not null references silos, name text not null, parent_id uuid null references elements,
  unique (mission_key, silo, name));

-- 0010_create_values                               cube_values: INSERT, SELECT only
create table parameters (key text primary key check (key ~ '^[a-z][a-z0-9_]{1,40}$'),
  label text not null, unit text not null, min_value numeric null, max_value numeric null,
  whole boolean not null default false);
create table unit_conversions (parameter text references parameters, unit text not null,
  multiplier numeric not null, value_offset numeric not null default 0,
  primary key (parameter, unit));                         -- the standard unit has multiplier 1
create table header_synonyms (header text check (header = lower(header)),
  parameter text references parameters, language text not null check (language in ('en','fr')),
  primary key (header, parameter));
create table cube_values (id, mission_key text not null references missions,
  silo text not null references silos, stage text null references stages,
  element_id uuid null references elements, parameter text not null references parameters,
  kind text not null check (kind in ('exact','range','unknown','not_applicable')),
  low numeric null, high numeric null, source_number text null, source_unit text null,
  valid_from date not null, recorded_at ts,
  item text not null, version int not null, block int not null, locator text not null,
  quote_sha256 bytea not null check (octet_length(quote_sha256) = 32),
  approval_request_id uuid null references approval_requests, simulated boolean not null,
  check ((kind in ('exact','range') and low is not null and high is not null)
    or (kind in ('unknown','not_applicable') and low is null and high is null)),
  check (kind <> 'exact' or low = high), check (low is null or low <= high));
create index on cube_values (mission_key, parameter, valid_from desc, recorded_at desc);
-- seeded values have no approval request; every other value names the request that accepted it

-- 0011_create_facet_texts                          craft_app: SELECT (indexing writes as craft_owner)
create table facet_texts (id, mission_key text not null references missions,
  facet text not null check (facet in ('purpose','design','operations','program','cost_and_risk')),
  text text not null, sources text[] not null, model text not null, built_at ts,
  unique (mission_key, facet));
```

## Indexing and changes

`craft index` brings the library up to date. It runs as `craft_owner`, like the seed, because the library is derived data.

1. Scan the provider.
2. Read the change feed after the stored cursor.
3. For each new version: convert it to chunks, write the summary, embed chunks and the document version, and record the block differences from the previous version by chunk text hash.
4. For each move: update the item's path and tags. For each delete: mark the item deleted.
5. Rebuild the facet texts and facet vectors of every mission whose documents changed.
6. Store the new cursor.
7. If `--enrich-ocr` is set, enqueue OCR for each active item's current PDF or image version only when it has no converted chunks and no stored OCR. Text-native and historical versions are not automatically submitted. Workers write `ocr_text`, `ocr_model` and `ocr_updated_at` on `doc_versions`.

Block matching preserves repeated text. Equal hashes at the same position are unchanged; remaining equal hashes pair in ascending position order and are moved. Remaining pairs at the same position are changed; unmatched blocks are added or removed. Differences are stored with the new version in the same transaction.

Running `craft index` twice in a row adds nothing. `craft index --rebuild` prepares converted text and model results before atomically replacing derived chunks, vectors and differences. Document-version identifiers and OCR for matching source hashes survive; failed preparation or replacement leaves the existing search index intact. Existing facet texts and vectors survive later OCR enqueue or facet-model failures, and are replaced only after facet preparation succeeds. The ingestion cursor advances only after those steps complete, allowing a later run to recover. Cube values and their citations are untouched.

`craft index --enrich-ocr` recovers eligible current versions after an enqueue outage. A scanned PDF or image without convertible text can have an OCR-only version record, with no chunks or embeddings. OCR stays outside retrieval ranking and embeddings. Explicit version requests can enrich historical PDF or image versions. Enqueue and retry atomically share one active OCR attempt per version, including forced attempts. Persisted text prevents normal re-enqueue even after job records expire.

OCR reads at most `CRAFT_OCR_MAX_BYTES` plus one byte before rejecting an oversized source; the default limit is 10 MiB. PDFs use the provider's document payload and images its image payload. Permanent source/provider rejections block automatic retry while their policy is retained. Transient failures use an exponential cooldown starting at `CRAFT_OCR_RETRY_SECONDS` (one hour by default), capped at one day, and stop after `CRAFT_OCR_ATTEMPT_LIMIT` automatic attempts (three by default). Terminal jobs, version claims, and retry policies expire after seven days; active claims do not expire. After terminal policy expiry, a later indexing request may start a new attempt budget. Persisted OCR text still prevents ordinary re-enqueue. A lost worker or missing persisted result also observes a cooldown; an administrator can request `POST /api/library/documents/{item}/versions/{version}/ocr` to recover a readable version even after job expiry or policy corruption. Explicit recovery may override retry limits but never source provenance, size limits, or active-job deduplication. Inference runs outside database transactions; final OCR persistence locks the active provider row and requires the indexed version hash to still match the captured source.

| Change in the provider | Library | Cube |
|---|---|---|
| Content edited | New document version with its chunks, vectors, summary and block differences. The old version stays for existing citations | Values citing the old version keep citing it. A changed table can be read again for new candidate values ([Intake and approval](datacube.md#intake-and-approval)) |
| Moved to another mission, silo or stage folder | Same item; tags updated | Values keep their mission |
| Renamed | Same item; title updated | None |
| Deleted | Versions stay for existing citations; the item leaves search | A value citing it shows `unknown, source withdrawn` ([Values and state on a date](datacube.md#values-and-state-on-a-date)) |

Search covers the latest version of each item that is not deleted. Older versions are reached through citations, `read_chunks` and `compare_versions`.

## Facets

A facet is one way of describing a mission. Each mission has at most one facet text per facet. A facet text has about 80 words.

| Facet | Covers | Fed mostly by silos |
|---|---|---|
| `purpose` | What the mission is for, its users and payload | requirements, reviews |
| `design` | The spacecraft: mass, power, thermal, structure, communications | engineering, requirements, quality |
| `operations` | Orbit, launch, ground segment and operations | launch, engineering |
| `program` | Schedule, stages, reviews and decisions | reviews, requirements |
| `cost_and_risk` | Estimates, contingency and risks | budget, reviews |

One model call per mission reads the summaries of the mission's documents and writes the facet texts. Each sentence ends with the item it came from in square brackets, such as `[itm_4f9c2a0b1c2d]`. A validator removes any sentence that names no item or names an item outside the mission. A facet with no remaining sentence has no row and counts as missing, never as zero. `sources` lists the items the facet text names.

Facet texts use active sources and their latest indexed summaries, with at most 80 words per facet. Changed versions, moves and withdrawals rebuild the affected missions. Each text has a `facet-v1` vector labeled with the embedding model. Reads recheck every cited source against indexed and live mission and silo grants. A withdrawn source, changed mission or missing current-model vector cannot contribute to a similarity score. Read operations make no conversational model call.

## Missions, silos and stages

| Record | Holds | Example |
|---|---|---|
| Mission | Key, title, current stage, simulated label | `msn-sim-01`, current stage `st3-design` |
| Mission alias | Another name a source uses for the mission | `sim-01` maps to `msn-sim-01` |
| Element | A named part of a mission within a silo, with an optional parent | power subsystem in `engineering` |

| Silo | Holds |
|---|---|
| `requirements` | What the mission must do |
| `quality` | How it is verified and assured |
| `launch` | Launch and orbit |
| `engineering` | How it is built |
| `reviews` | Reviews and decisions |
| `budget` | Costs and estimates |

Stages are the mission lifecycle. Each mission points at its current stage. A stage key never changes meaning.

| Key | Stage | The stage ends when | Typical documents |
|---|---|---|---|
| `st1-concept` | Concept | The mission need and options are agreed | Need statements, concept studies |
| `st2-definition` | Definition | System requirements are baselined | Requirements, interface drafts |
| `st3-design` | Design | The design review passes | Design documents, mass and power budgets |
| `st4-build` | Build | Units are built and integrated | Build records, procurement records |
| `st5-verification` | Verification | The test campaign is complete | Test plans and reports |
| `st6-operation` | Operation | The mission ends | Operations and anomaly reports |
| `st7-closure` | Closure | Disposal and lessons are recorded | Close-out reports |

The seed sets each mission's current stage. `craft missions stage <mission key> <stage key>` changes it and runs as `craft_owner`. A query can ask for the current stage of each mission with the stage `current`.

## Parameter dictionary and units

Each parameter has a key, a label, a standard unit, allowed bounds and whether it is whole. Other units convert to the standard unit on intake with `standard = source × multiplier + offset`.

| Key | Label | Standard unit | Allowed | Converts from |
|---|---|---|---|---|
| `mass_dry` | Dry mass | kg | 0 or more | g, t, lb |
| `power_peak` | Peak power | W | 0 or more | kW, mW |
| `payload_volume` | Payload volume | m3 | 0 or more | L, cm3 |
| `orbit_altitude` | Orbit altitude | km | 0 or more | m |
| `readiness` | Technology readiness | level | 1 to 9, whole | none |
| `design_life` | Design life | year | 0 or more | month |

1. The source number, its unit and the conversion are kept beside the converted value.
2. No unit, no value. A number without a unit is shown as a problem.
3. A unit is never guessed from the size of a number.
4. A key never changes meaning. A new meaning gets a new key.
5. A header synonym maps a source header, lower-cased with its unit in parentheses removed, to a parameter. "Masse sèche (kg)" maps to `mass_dry` with unit kg.

## Values and state on a date

| Field | Meaning | Example |
|---|---|---|
| Mission, silo, stage, element | Where the value belongs | `msn-sim-01`, `engineering`, `st3-design`, power subsystem |
| Parameter | Dictionary key | `power_peak` |
| Kind | `exact`, `range`, `unknown` or `not_applicable` | `exact` |
| Low, high | The value in the standard unit; equal for an exact value | 450, 450 |
| Source number and unit | As written in the source | `0.45`, `kW` |
| Valid from | The date the source says the value applies from | 2026-03-01 |
| Recorded at | When CRAFT accepted it | 2026-10-15 14:02 UTC |
| Citation | Item, version, block, locator and quote hash ([Library: chunks, summaries and citations](datacube.md#library-chunks-summaries-and-citations)) | |
| Approval | The approval request that accepted it; none for seeded values | |
| Simulated | The simulated label | true |

A value exists only after a person approved it, or the seed loaded it. Values are append-only. Unknown and not applicable are never zero.

The logical cell is mission, silo, stage, element and parameter. A state request gives `at` (a date) and `knownAt` (a time); both default to the time of the request and are returned with the result.

| Behavior | Does | Never does |
|---|---|---|
| Pick the value | In each logical cell, takes the value with the latest `valid_from` not after `at`, among values recorded at or before `knownAt`. A tie goes to the latest `recorded_at`, then the highest identifier | Average or merge two values |
| Source withdrawn | If the picked value's item is deleted in the provider, shows `unknown, source withdrawn` | Fall back to an older value |
| Unknown replaces a number | A later `unknown` value wins over an older number | Keep showing the old number |

Example: peak power 400 W valid from 1 January (version 1) and 450 W valid from 1 March (version 2). On 15 February the state is 400 W citing version 1. On 15 March it is 450 W citing version 2. On 15 March after the item is deleted, it is `unknown, source withdrawn`.

## Library search and exploration

`search` runs the [Retrieval](documents.md#retrieval) hybrid search over chunks and document summaries.

1. Access first ([Access](datacube.md#access)), then the mission, silo, stage and kind filters.
2. Keyword and vector candidates, 50 each, fused with the normalized 0.7 semantic and 0.3 lexical weights.
3. At most two chunks from the same document section (heading path) in the results.
4. Up to 8 results. Each chunk result carries the chunk before and after it in block order.

`explore` widens the window around a chunk: up to 10 blocks before and after, in the same document version. With a query, the window's chunks are returned with their scores against it.

| Operation | Does | Never does |
|---|---|---|
| `search` | Ranks chunks and document summaries within the filters and returns citation handles | Return text the caller cannot read |
| `get_document` | Returns a document version's tags, labels, summary, outline and version list | Return the original bytes |
| `read_chunks` | Returns up to 20 consecutive chunks of one document version | Return chunks from another version |
| `explore` | Returns the chunks around a handle | Leave the handle's document version |
| `check_citation` | Re-reads the cited version from the provider and compares the quote hash. Returns `current`, `changed`, `newer_version` or `withdrawn` | Change a stored chunk |
| `compare_versions` | Lists blocks added, removed, changed or moved between two versions | Write a merged document |
| `preview` | Streams the original at the cited version, opened at the locator; HTTP only | Store the bytes in CRAFT |
| `download` | Streams the original at a version as an attachment; HTTP only | Store the bytes in CRAFT |

Version comparisons return at most 500 differences, with a truncation flag. Consecutive comparisons use stored differences; other pairs use the same hash rules on demand. Equal versions have no differences. Missing versions are not found.

Citation checks read the exact provider version after both indexed and live mission access pass. Live deletion gives withdrawn. The freshly converted block or cited table row supplies the quote hash; a missing or mismatched quote gives changed. A matching older quote gives newer_version, including versions not yet indexed. The result includes the latest provider version. Cached chunks remain untouched.

## Parametric search, comparison and evidence

| Operation | Does | Never does | Example |
|---|---|---|---|
| `list_parameters` | Returns the dictionary with units and conversions | Guess a key | Before a numeric filter |
| `list_missions` | Resolves names and aliases to readable missions, with their current stages | Count hidden missions | "sim-01" |
| `find_missions` | Filters missions by 1 to 12 parameter conditions joined with AND, at a date. Up to 20 results | Match an unknown value; run a free-form query | Dry mass at most 150 kg and orbit at most 600 km |
| `get_mission` | Returns a mission's values at a date, with gaps shown | Fill a gap | `msn-sim-01`, engineering, current stage |
| `compare_missions` | Puts 2 to 5 missions side by side for the same parameters and scope | Compare different elements as if equal | Two missions, mass and power |
| `get_value_evidence` | Returns a value's citation, quote, source number, unit, conversion and citation status | Return an unreadable source | The 450 W value |

A condition names a parameter, an operator (`eq`, `ge`, `le` or `between`), a value and its unit. Values and units convert before the query is built. An unknown value never matches. A range matches only if its whole interval satisfies the condition.

All conditions match within one mission, silo, stage and element. Comparison uses a mission aggregate when present. Otherwise, elements must have the same name and silo. An ambiguous selection is a gap.

## Similar missions

`similar_missions` ranks readable missions by their facet texts.

1. Readable missions are selected (access first).
2. For each chosen facet (all five by default), the cosine similarity between the two missions' facet vectors.
3. A facet counts only if both missions have it and the caller can read every item its facet text names.
4. The score is the mean over the facets that count. A pair with fewer counting facets than half the chosen facets, rounded up, is listed as not comparable.
5. Comparable missions are sorted by score, highest first, then by key. Not comparable missions follow. At most 10 results; 5 by default.

`explain_similarity` returns, for each chosen facet, the score and both facet texts with the items they name.

Facet selections contain one to five distinct names. Missing or unreadable facet texts return a null side and no source identifiers. Missing, zero or nonfinite vectors produce a null score. Cosine scores preserve negative values; the mean includes only the facets available on both sides.

| Mission | Score | design | operations | cost_and_risk |
|---|---|---|---|---|
| `msn-sim-04` | 0.86 | 0.91 | 0.88 | 0.79 |
| `msn-sim-07` | 0.71 | 0.80 | 0.62 | missing |

## Intake and approval

| Step | Approval | Why |
|---|---|---|
| Library indexing | None | Derived from the provider and rebuildable |
| Candidate values from a table chunk (`prepare_values`) | None | A read: nothing is written |
| Values into the cube (`submit_values`) | The requester approves the batch | The cube is CRAFT's accepted record |
| Seeded values | None; loaded as simulated | Demonstration data |

`prepare_values` reads one table chunk. Each header maps to a parameter through the header synonyms. Each cell becomes a candidate with its row citation, source number and unit, converted value and any problem. The mission comes from the chunk's tags unless the request names it. Problems: unknown header, no unit, unknown unit, not a number, outside the allowed bounds, mission not resolved, no valid-from date.

Parameter headers keep their explicit unit in parentheses; an inline cell unit takes precedence. Recognized parameter headers are converted in every column. Other first columns label rows, and known element columns can contain parameter-labelled rows. Explicit mission-total rows have no element; other element names stay named. Element lookup applies every mission and silo grant, including unspecified scope.

Each candidate has at most one problem, in the order above. The response lists all applicable context problems. Missing mission and date remain visible; a date is never guessed from another block. Empty cells and TBD are unknown, and n/a is not applicable. Numeric bounds use the registered Decimal conversion, bounds and whole-number rule. The row citation preserves escaped source text and its absolute spreadsheet row or table-window row. Nothing is written.

`submit_values` takes the candidates without problems. The approval card shows each candidate's source cell, original number and unit, converted value, mission, element and date. After approval, the values are written in one transaction, each naming the approval request.

## Access

| Rule | Applies to |
|---|---|
| Every account, including an admin, needs mission grants for cube reads. A grant in `mission_grants` with a silo covers that silo only; a grant without one covers every silo | Every library and cube operation |
| Reference material (no mission) is readable by every account; an admin reads everything | Library |
| A chunk, document version or value is readable only if its mission and silo are readable | Library and cube |
| Access applies before filtering, ranking and counting | Search, find, compare, similar |
| Results never say how many hidden items were removed | Every list |

`craft users grant <username> <mission key> [--silo <silo>]` and `craft users revoke` change grants.

## Operations and tools

Every operation below is declared with `@operation` ([Backend conventions](development.md#backend-conventions)) and serves HTTP, the command line and Python. Every one with a tool name is also an MCP tool through the adapter in [Tools over MCP](agent.md#tools-over-mcp); no list of tools is kept by hand. Reads have the effect `NONE`. Read tools run no conversational model; embedding steps remain private.

| Operation | Route | Tool | Effects |
|---|---|---|---|
| `library.search` | `POST /api/library/search` | `search` | `NONE` |
| `library.get_document` | `GET /api/library/documents/:item` | `get_document` | `NONE` |
| `library.read_chunks` | `GET /api/library/documents/:item/chunks` | `read_chunks` | `NONE` |
| `library.explore` | `POST /api/library/explore` | `explore` | `NONE` |
| `library.check_citation` | `POST /api/library/citations/check` | `check_citation` | `NONE` |
| `library.compare_versions` | `GET /api/library/documents/:item/compare` | `compare_versions` | `NONE` |
| `library.preview` | `GET /api/library/documents/:item/preview` | | `NONE` |
| `library.download` | `GET /api/library/documents/:item/download` | | `NONE` |
| `cube.list_parameters` | `GET /api/cube/parameters` | `list_parameters` | `NONE` |
| `cube.list_missions` | `GET /api/cube/missions` | `list_missions` | `NONE` |
| `cube.find_missions` | `POST /api/cube/find` | `find_missions` | `NONE` |
| `cube.get_mission` | `GET /api/cube/missions/:key` | `get_mission` | `NONE` |
| `cube.compare_missions` | `POST /api/cube/compare` | `compare_missions` | `NONE` |
| `cube.get_value_evidence` | `GET /api/cube/values/:id` | `get_value_evidence` | `NONE` |
| `cube.similar_missions` | `POST /api/cube/similar` | `similar_missions` | `NONE` |
| `cube.explain_similarity` | `POST /api/cube/similar/explain` | `explain_similarity` | `NONE` |
| `cube.prepare_values` | `POST /api/cube/candidates` | `prepare_values` | `NONE` |
| `cube.submit_values` | `POST /api/cube/values` | `submit_values` | `DATA` |

The API contract defines the request and response forms. Logs record handles, scores and counts only, never query or chunk text.

## Seed data

| Command | Loads |
|---|---|
| `craft seed base` | Demo accounts and their mission grants, and a small provider folder of Markdown documents in two missions and the reference folder |
| `craft seed demo` | The demo set: 12 simulated missions in four families of three, about 120 documents in every allowed format across silos and stages, the cube records, header synonyms and seeded values, and a ground-truth file |

`seed base` leaves indexing to `craft index` and provisions no account password. `seed demo --seed 1` performs indexing and seeded-value loading itself, then refreshes the index after its source withdrawals. These host commands use owner database access and preserve existing versions and values.

The demo set is generated, not stored: the same seed number gives the same bytes. It includes version pairs, renamed copies, moved and deleted documents, mixed units, English and French headers, unknown values and a value whose source is withdrawn. Every mission, document and value is labelled simulated. No name, value or document number comes from a real source.

The ground-truth file, `<provider root>/.provider/ground-truth.json`, lists what the generator planted: questions with the citation handles that answer them, the mission families, the version changes and the aliases. [Retrieval](documents.md#retrieval)'s evaluation reads it.

A fresh demo set has 125 source items and 48 planted questions. Twelve sources have changed versions with later valid dates; two sources move between stage folders; three copies keep matching cells with different bytes. Three cited sources are withdrawn after seeded values name them. Mission-total rows load aggregate values. A unitless cell is not loaded, and TBD is unknown.

The ground-truth file at `.provider/ground-truth.json` records source handles, families, changes and dated values. File properties and package order are fixed. Repeating an unchanged completed seed adds nothing. Changing its baseline creates a new generation and preserves earlier versions and values.

## Document summary and owned inspection

`library.inspect_file` is a signed-in Python-only operation with effect `NONE`. It converts supplied bytes in memory through the same format converter, returning their exact hash and ordered blocks with hashes, headings and locators. It has no route, tool or domain write.

`POST /api/summarize` selects an exact readable item and integer version with its original-byte hash. `POST /api/summarize/upload` accepts one explicitly simulated PDF, Word file or workbook. Source reads and conversion finish before the private model step. A source supports a summary sentence only when the complete sentence or table row matches; citation identity alone is insufficient. Uploaded citations name the original-byte hash and ordered blocks, with no invented provider item. Selected citations keep library handles.

The response holds cited extracts and uncommitted mission, silo and stage suggestions. Selected tags use provider-path evidence. Upload suggestions use bounded literal labels in source text and headings. Missing or conflicting values are null; conflicting evidence remains visible. Originals are not indexed, filed or persisted. Files are at most 25 MB and converted summary input is at most 100,000 characters. Confidence follows the calibration gate in [Models and confidence](agent.md#models-and-confidence).

## Database

Structured Query Language (SQL) defines the database tables. Identifiers are universally unique identifiers (UUIDs). Token and source digests use the 256-bit Secure Hash Algorithm (SHA-256). The application programming interface (API) uses Hypertext Transfer Protocol (HTTP). Agent-User Interaction (AG-UI) describes stored messages. The command-line interface (CLI) uses application operations.

- Application records in PostgreSQL:
  - SQLAlchemy tables and queries, Psycopg driver, Alembic migrations, pgvector for vectors;
  - sync database code, with handlers in FastAPI's thread pool.
- Services select short database transactions, read-only for reads. Storage receives an explicit Session and never commits independently.
  - No transaction spans a model call, retrieval outside the database or approval wait. Cross-feature calls retain trusted context and resource permissions.
- Database logins: `postgres` (superuser) only runs `app/infrastructure/database/initdb.sh`. Migrations run as `craft_owner`; the app connects as `craft_app`.
  - `craft_app` gets `SELECT` by default and can't create tables. Each migration grants writes per table.
  - Append-only tables grant `craft_app` only `INSERT`, `SELECT`.

### Logins

| Login | Used by | Rights |
|---|---|---|
| `postgres` | `app/infrastructure/database/initdb.sh`, once, on an empty volume | Superuser. Needed to create the vector extension |
| `craft_owner` | Migrations and the seed command | Owns `craft_dev` and `craft_test`; creates and changes tables |
| `craft_app` | The running application, tests, command line | Reads every table by default. Writes only where a migration grants it. Cannot create tables |

### Migrations and tables

- Migrations:
  - files `app/infrastructure/database/migrations/versions/NNNN_<verb>_<thing>.py`, revision ID = `NNNN`;
  - one linear history, never edited after merge;
  - applied with `uv run alembic upgrade head`, never at startup.
- Defaults:
  - `id` = `uuid primary key default uuidv7()` (time-ordered, built into PostgreSQL);
  - `ts` = `timestamptz not null default now()`;
  - every column is `not null` unless it says `null`.

Tables and columns use the stated defaults. In the SQL, `id` is the UUID default above and `ts` is the timestamp default above. Each migration comment lists the application write grants. Account roles, conversation ownership and append-only records follow the rules in this section. Adding values to a database check constraint requires a migration. Partial unique indexes permit one decision and one execution per approval.

```sql
-- 0001_create_accounts                              craft_app: INSERT, UPDATE (user_roles: INSERT, DELETE)
create table users (id, username text not null unique check (username = lower(username)),
  display_name text not null, disabled_at timestamptz null, created_at ts);
create table local_credentials (user_id uuid primary key references users,
  password_hash text not null, updated_at ts);                                     -- Argon2id
create table user_roles (user_id uuid references users,
  role text check (role in ('member','admin')), granted_at ts, primary key (user_id, role));
create table auth_sessions (id, user_id uuid not null references users,
  token_sha256 bytea not null unique, created_at ts, last_seen_at ts,
  idle_expires_at timestamptz not null, expires_at timestamptz not null,
  revoked_at timestamptz null, revoked_reason text null);
create index on auth_sessions (user_id);

-- 0002_create_call_log                              craft_app: INSERT, SELECT
create table call_log (id bigint generated always as identity primary key, at ts,
  kind text check (kind in ('operation','step')), name text not null,
  origin text check (origin in ('http','mcp','python')), outcome text check (outcome in ('ok','error')),
  error_code text null, duration_ms int not null, db_statements int not null default 0,
  db_ms int not null default 0, request_id uuid not null, call_id uuid not null,
  parent_call_id uuid null, user_id uuid null, auth_session_id uuid null,
  conversation_id uuid null, run_id uuid null, tool_call_id text null);    -- no foreign keys, no content
create index on call_log (at); create index on call_log (user_id, at);
create index on call_log (run_id); create index on call_log (request_id);

-- 0003_create_conversations                         conversations: INSERT, UPDATE; messages: INSERT, SELECT
create table conversations (id, owner_id uuid not null references users, title text not null,
  created_at ts, updated_at ts, archived_at timestamptz null);
create index on conversations (owner_id, updated_at desc);
create table messages (id, conversation_id uuid not null references conversations, seq int not null,
  role text check (role in ('user','assistant','tool','activity')),
  content jsonb not null, run_id uuid null, created_at ts,                -- content: the AG-UI message
  unique (conversation_id, seq));

-- 0004_create_agent_runs                            craft_app: INSERT, UPDATE
create table agent_runs (id, conversation_id uuid not null references conversations,
  started_by uuid not null references users,
  status text check (status in ('running','paused','finished','failed','cancelled')),
  model text not null, instructions_version text not null,
  token_sha256 bytea null unique, token_expires_at timestamptz null,
  started_at ts, ended_at timestamptz null);
create table agent_sessions (conversation_id uuid primary key references conversations,
  state jsonb not null, updated_at ts);                                    -- AgentSession.to_dict()
alter table messages add foreign key (run_id) references agent_runs;

-- 0005_create_approvals                             craft_app: INSERT, SELECT only
create table approval_requests (id, run_id uuid not null references agent_runs,
  conversation_id uuid not null references conversations, requested_by uuid not null references users,
  operation text not null, arguments jsonb not null, arguments_sha256 bytea not null,
  evidence jsonb not null, reasoning text not null, retrieval_path jsonb not null,
  interrupt_id text not null unique, created_at ts);
create table approval_events (id, request_id uuid not null references approval_requests,
  kind text check (kind in ('requested','approved','rejected','executed','failed')),
  actor_id uuid null references users, at ts, note text null);              -- null actor = the system
create unique index on approval_events (request_id) where kind in ('approved','rejected');
create unique index on approval_events (request_id) where kind in ('executed','failed');
create view approval_status as select distinct on (request_id) request_id, kind, actor_id, at
  from approval_events order by request_id, at desc, id desc;

-- 0006_create_library                              craft_app: SELECT (indexing writes as craft_owner);
--                                                  mission_grants: INSERT, DELETE
create table provider_items (item text primary key check (item ~ '^itm_[0-9a-f]{12}$'),
  path text not null, title text not null, mission_key text null, silo text null, stage text null,
  simulated boolean not null,
  classification text check (classification in ('unclassified','protected-a','protected-b')),
  deleted_at timestamptz null, updated_at ts);                  -- tags come from the provider path
create table doc_versions (id, item text not null references provider_items, version int not null,
  sha256 bytea not null check (octet_length(sha256) = 32), media_type text not null,
  summary text not null, summary_kind text check (summary_kind in ('extract','generated')),
  summary_tsv tsvector generated always as (to_tsvector('english', summary)) stored,
  indexed_at ts, unique (item, version));
create index on doc_versions using gin (summary_tsv);
create table chunks (id, doc_version_id uuid not null references doc_versions on delete cascade,
  block int not null, kind text check (kind in ('text','table','slide','sheet')),
  heading_path text not null, locator text not null, text text not null,
  text_sha256 bytea not null check (octet_length(text_sha256) = 32),
  tsv tsvector generated always as (to_tsvector('english', heading_path || ' ' || text)) stored,
  unique (doc_version_id, block));
create index on chunks using gin (tsv);
create table vectors (id, owner_kind text check (owner_kind in ('chunk','doc_version','facet_text')),
  owner_id uuid not null, model text not null, recipe text not null,
  embedding vector(1536) not null, unique (owner_kind, owner_id));
create index on vectors using hnsw (embedding vector_cosine_ops);
create table version_diffs (from_version uuid references doc_versions on delete cascade,
  to_version uuid references doc_versions on delete cascade, seq int not null,
  change text check (change in ('added','removed','changed','moved')),
  from_block int null, to_block int null, primary key (from_version, to_version, seq));
create table index_cursor (provider text primary key, cursor bigint not null, updated_at ts);
create table mission_grants (id, user_id uuid not null references users, mission_key text not null,
  silo text null, granted_at ts, unique nulls not distinct (user_id, mission_key, silo));

-- 0007_create_user_documents                        craft_app: INSERT, SELECT only
create table drafts (id, owner_id uuid not null references users, title text not null,
  body text not null, based_on uuid null references drafts,
  approval_request_id uuid null references approval_requests, created_at ts);
create index on drafts (owner_id, created_at desc);                         -- an edit is a new row

-- 0008_create_workspace_files (s1)                 craft_app: INSERT, SELECT, UPDATE, DELETE
create table workspace_files (id, conversation_id uuid not null references conversations,
  name text not null, media_type text not null, size_bytes int not null, sha256 bytea not null,
  bytes bytea null, origin text check (origin in ('upload','agent')),
  state text check (state in ('uploading','processing','ready','failed','export-pending','exported')),
  simulated boolean not null,
  classification text check (classification in ('unclassified','protected-a','protected-b')),
  status_detail text null, export_approval_id uuid null references approval_requests, created_at ts);
create index on workspace_files (conversation_id, created_at desc);    -- deleted when the conversation is archived
```

### Table rules

| Area | Rules |
|---|---|
| Accounts | Session token digests and password hashes only. Accounts by CLI. Roles = rows in `user_roles` |
| Call log | Holds linked account, sign-in session, conversation, run and tool-call identifiers without content. Approval records hold decisions separately. |
| Conversations | Owned by an account. Sign-out deletes nothing. History comes from `messages` as stream-protocol messages. A later run restores `agent_sessions.state` with `AgentSession.from_dict`. |
| Agent runs | Only the run-token digest is stored. A running run finishes, fails, cancels or pauses; a decision can resume a paused run. |
| Approvals | Every step a new row; current state = latest event; the database refuses edits and deletes |
| Library | Derived from the document provider and rebuilt by `craft index` ([Indexing and changes](datacube.md#indexing-and-changes)). Never holds an original file. A chunk is the unit of search and citation; a citation names item, version, block, locator and quote hash. `simulated` and `classification` label every chunk and citation. Access comes from `mission_grants` ([Access](datacube.md#access)) |
| Workspace | The files of one conversation, uploaded or made by the agent. A file is simulated only if all its inputs are, and takes the highest classification among them. Deleted when the conversation is archived; keeping one or sending it outside CRAFT is an export, which needs approval. Every download is in the call log |
| User documents | `drafts` belong to their owner. Append-only. Shared documents (Vision) build on this pattern |

Answer retrieval records live in `messages.content` as typed `craft.retrieval` AG-UI activities beside `craft.answer`. The activity holds messageId, runId, model, instructionsVersion and path. No new table is needed. File operations use the existing workspace table and declared application grants. Limits are 25 MiB per file and 50 files; conversation-row locks serialize counting and updates. Archive and run start share account serialization. Archive refuses active runs, removes workspace rows and retains history.

Migration 0012 adds owner-scoped working notes. Migration 0013 adds owned skill heads and append-only `skill_revisions`. An owner/mutation identifier is unique, and saving checks the expected head revision. These records are separate from conversation files and the provider originals.

Migration 0015 adds `workspace_tokens` ([Workspace tokens](access.md#workspace-tokens)) and records each call's workspace token in `call_log.workspace_token_id`, with the origin `workspace`. Only token digests are stored. Existing workspace call rows make the downgrade fail rather than remove audit rows.

### Seed and health

- Seed is a host command, separate from migrations. `uv run craft seed base` loads synthetic accounts, grants, two missions and a small provider folder; it does not set account passwords or build the index. `uv run craft index` builds the derived library. `uv run craft seed demo --seed 1` generates the larger set, indexes it, loads seeded values, records withdrawals and refreshes the index ([Seed data](datacube.md#seed-data)). Owner database access handles seed and index writes. An unchanged completed seed adds no source version or value. Account creation and grants through `craft users` use the application login.
- Liveness returns HTTP 200 with an ok body while the process runs. Readiness returns HTTP 200 when the database reports the expected migration within three seconds; otherwise it returns HTTP 503 with an unavailable body. Its separate connection has a three-second deadline. Both routes are anonymous, outside `/api`, and reveal no protected information. Hosted probes use the same split.
- [Deferred](roadmap.md#deferred) defines database deployment, permission, backup, retention and audit extensions. [wiki home](README.md) defines the workspace and sandbox scope.
