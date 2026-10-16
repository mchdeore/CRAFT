# 8. Engineering datacube

The application programming interface (API) uses Hypertext Transfer Protocol (HTTP) and JavaScript Object Notation (JSON). Tool calls use the Model Context Protocol (MCP). Identifiers are universally unique identifiers (UUIDs), and times use Coordinated Universal Time (UTC). Package readers use Extensible Markup Language (XML); text sources include Hypertext Markup Language (HTML), Portable Document Format (PDF) and comma-separated values (CSV). Schema blocks use Structured Query Language (SQL). Result limits use kibibytes (KiB).

## 8.1 Purpose and scope

The engineering datacube records source-backed technical parameters for missions. It supports parametric search, comparison and document research in chat and in the browser. It records what a source says. It does not change a mission's design or replace its engineering authority.

The first release uses labelled simulated data. It covers a parameter dictionary, missions and options, technical values, evidence, mission similarity and documents. Section 12 defines the triggers for the later releases in section 8.21.

## 8.2 Model

A mission is the main record. Each mission has silos for requirements, quality, launch, engineering, reviews and budget. A silo groups one kind of context. It links parameters and documents rather than holding another copy of a document.

A stage identifies when an engineering activity belongs in the lifecycle. The stage table begins with concept, definition, design, build, verification, operation and closure. Stages are replaceable reference data. These seven stages are synthetic. Their identifiers stay stable for records that already use them. A replacement list adds rows and records a mapping; it does not reinterpret old records.

Valid time says when a value applies to the mission. Recorded time says when CRAFT learned it. Section 8.8 defines how both times select a state.

## 8.3 Field jobs

Each field has one job. A filter field narrows a set. A search field ranks a result. A fact field supplies evidence for a statement.

| Job | Fields | Behavior |
|---|---|---|
| Filter | Mission, option, element, class, parameter, silo, stage, marker and basis | Exact validated conditions; no authority to grant access |
| Search | Document keyword/vector fields and parametric distance | Rank the readable filtered set |
| Fact | Source values, units, dates, locators, confidence and recipes | Show and cite the stored observation |

Field jobs can produce measurements, requirements and verification documents. The mission, silo and stage context links them through the section 7 documents. A later gate baseline identifies the versions used in an engineering decision.

## 8.4 Layers

| Layer | Responsibility |
|---|---|
| Originals | Section 7 references to source files in their repositories |
| Reference data | Units, parameters, classes, silos, stages and similarity definitions |
| Evidence records | Missions, options, elements, document links and dated values |
| Derived index | Rebuildable document vectors, keyword index and measured caches |
| Operations | Access checks, state selection, bounded searches and calculations |
| Presentation | The same results in chat and in the browser |

Section 5 supplies the operation, observation and logging pipeline. Section 6 supplies the component and transaction rules. The datacube uses those rules throughout.

## 8.5 Parameter dictionary and units

Each parameter has a stable key, a label, a physical dimension, a canonical unit, a scale, bounds and header synonyms. Categories group related parameters. The dictionary exposes the unit conversions and bounds used by intake and search.

| Key | Meaning | Canonical unit | Scale | Domain | Header synonyms |
|---|---|---|---|---|---|
| mass | Mass | kg | Linear | Nonnegative | mass; masse |
| power | Power | W | Linear | Nonnegative | power; puissance |
| payload_volume | Payload volume | m3 (cubic metres) | Linear | Nonnegative | payload volume; volume de charge utile |
| readiness | Technology readiness | Level | Ordinal | Integers from 1 to 9 | readiness; maturité technologique |

| Unit | Dimension | Multiplier | Offset |
|---|---|---|---|
| kg | Mass | 1 | 0 |
| g | Mass | 0.001 | 0 |
| W | Power | 1 | 0 |
| kW | Power | 1000 | 0 |
| m3 | Volume | 1 | 0 |
| level | Readiness | 1 | 0 |

Numeric intake converts a source value with `canonical = source * multiplier + offset`. A conversion requires the same dimension. A source unit is never inferred from magnitude. A unitless or ambiguous numeric column produces a candidate with an error, rather than an accepted number. The evidence keeps the source locator and conversion recipe.

Linear scales support numeric distance. Logarithmic scales require strictly positive values and an explicit transformation in the definition. Ordinal scales require an explicit encoding. Nominal scales support equality filters but do not enter numeric distance. The first similarity method uses linear parameters only. The synthetic definition uses mass weight 0.6 and power weight 0.4, population scales 10 kg and 20 W, coverage floor 0.6, distance cutoff 100 and five results, with a maximum of 20. Changing a parameter's meaning creates a new key. Approved synonym rows may be added without changing old value interpretations.

## 8.6 Values with evidence and confidence

A value belongs to one mission, silo, element, parameter and stage. The element supplies any option context. It names a section 7 document, its content hash, a stable locator, a parsing recipe and an approval request. Every returned cell has a value identifier that can be fetched for evidence.

Markers distinguish an exact value, a range, a lower bound, an upper bound, to be determined and not applicable. Unknown and not applicable are never zero. Range endpoints are inclusive. Arithmetic and similarity use exact values only. Parametric filters admit a bound or range only when its entire possible interval satisfies the filter.

The basis distinguishes observed, assumed, imputed and derived values. The first release accepts observed and explicitly labelled assumed values. Imputation and derived estimates belong to later releases. A derived value records every input identifier, the method and its version. It inherits the highest input classification and is readable only when every input is readable.

Value confidence describes intake evidence. It is not answer confidence. Section 4 governs answer confidence. Missing cells reduce coverage. Conflicting sources reduce agreement. A number without a readable value identifier has no support. Similarity coverage and the number of comparable missions inform retrieval quality. Assumptions and low intake confidence appear beside the result.

The simulation label agrees with the mission and evidence document. A calculation never mixes simulated and real inputs. Withdrawn evidence creates a gap in current results. State is selected before checking withdrawal so a withdrawn current value cannot expose an older value as current.

## 8.7 Missions, options, elements and classes

A mission has a stable identifier and key. Aliases map alternative source labels to that identity. An unresolved alias or conflicting mission identity stops intake for that candidate. Similar titles alone do not establish identity.

An option belongs to one mission. An element belongs to one mission and silo, with an optional option and parent element. A composite foreign key prevents an option from being attached to another mission. Parent links stay in the same mission and silo. Intake checks the parent graph for cycles. Classes describe the kind of element rather than its name.

Search and comparison state the element scope. Mission-root values and option-specific values are different cells. A query never silently adds or averages them. A comparison uses the same parameter, class, stage and option scope for every mission. A missing counterpart appears as a gap.

## 8.8 Time and state on a date

A state request gives `validAt` and `knownAt`. Both default to the server's request time. The times are fixed once for a multi-call comparison and returned with the result.

Select rows with `valid_from <= validAt`, an open end or `valid_to > validAt`, and `recorded_at <= knownAt`. Within each logical cell, choose the row ordered by valid start, recorded time and identifier, all descending. The logical cell is mission, silo, element, parameter and stage. A correction with the same valid start wins by recorded time; the identifier resolves a remaining tie.

A later record can describe an earlier valid period. It changes today's account of that period but cannot change a view of what CRAFT knew before it was recorded. An unknown marker can replace an exact value. It must be selected before numeric filtering. Evidence availability is evaluated at `knownAt`; current research excludes withdrawn evidence. A historical view labels an unavailable source and cannot preview it.

Value state is part of the first release. Document revision matching and frozen gate baselines are the later work named in section 12.

## 8.9 Parametric search

The search form selects mission keys, silo, stage, element class, option scope and simulation label. It contains up to 12 parameter predicates. Each predicate uses a dictionary key, operator, value and source unit. The operations validate and convert predicates before building a parameterized query. There is no executable free-form query field.

Predicates are joined with AND. Operators are equality, greater than or equal, less than or equal, and inclusive range. All predicates refer to one selected element scope. Unknown cells do not match. A range matches only if both endpoints satisfy the condition. An unbounded interval cannot satisfy a finite range.

Access selects the readable set before filters, ranking or counts. Logical-cell selection suppresses an older row even when the selected row has unreadable evidence. The search returns at most 20 missions with matched cell identifiers, gaps, units and evidence handles. It gives only the number returned and a truncation flag. It does not report how many inaccessible missions or values were removed. Mission lists follow the contract's newest-first ordering; equal timestamps use the identifier descending.

## 8.10 Similar missions

Similarity uses a sealed version of an editable definition. The definition names its method, parameter keys, normalized positive weights, positive scales, reference value identifiers, simulation label, coverage floor, result count and distance cutoff. Editing creates a new version through approval. Browser editing and calibration are the section 12 extension.

The first method is weighted normalized Euclidean distance. Its scales are population standard deviations over a declared reference set, in canonical units. The scales are frozen when the definition is approved. The definition is available only if the account can read every reference value and its evidence. An account cannot obtain hidden-source statistics through a definition. A reference set with no spread cannot supply that parameter's scale. The definition must remove that parameter and normalize its remaining weights before sealing.

For a pair, common parameters have readable exact values on both sides. Coverage is their weight sum. A pair below the definition's coverage floor is not comparable. For each common parameter, the squared contribution is its weight multiplied by the squared difference divided by its scale squared. Distance is the square root of the contribution sum divided by coverage. This renormalizes the common weights without filling missing values.

Each contribution share is its squared contribution divided by the total. An identical pair has distance zero and shares zero. Results include the definition identifier and version, frozen scales, coverage, skipped keys, per-parameter contributions and value handles. Sort by distance ascending, then mission identifier ascending. Apply the cutoff and count after access and comparison. A partial pair is visibly partial, even when it ranks first.

Category, silo and stage embeddings later add a separate method with its own definition and evaluation. They do not replace the numeric baseline or section 10's document ranking.

## 8.11 Documents in the cube

Mission documents are links to the documents and original references in section 7. The link carries mission, silo, stage and original content hash. A document may have several links. The datacube stores no document bytes and provides no document editing route.

Document search calls section 10 through the document operation. Readable mission, silo and stage links constrain the document set before keyword and semantic candidates are ranked. The search uses normalized 0.7 semantic and 0.3 lexical fusion, deduplicates passage identifiers and returns citations in the existing contract shape. The link cannot grant document access.

Preview and download call the existing document routes with version, artifact and locator. Section 7 owns original-file versions and their stored bytes. Original references also identify the source repository and artifact. A missing original produces a safe unavailable result. It is never replaced by a silently generated document.

## 8.12 Chat and browser

The browser provides a mission list, parameter search form, comparison table, similarity explanation and evidence panel. Cells show units, markers, basis, simulation label and intake confidence. Evidence opens the existing preview. The browser does not recalculate a distance or infer a missing value.

Chat calls the same operations. An answer can show a compact result table and link each figure to its value and original document. Follow-up calls use the returned identifiers and fixed time fields. Candidate intake and a JSON view export show an approval card before a write. An exported workspace file follows section 7's conversation lifecycle and the contract's export gate.

## 8.13 Operations

The application programming interface (API) uses Hypertext Transfer Protocol (HTTP) routes under `/api`. JavaScript Object Notation (JSON) fields use camelCase. Python fields use snake_case. Identifiers are universally unique identifiers (UUIDs). Times use the contract's Coordinated Universal Time (UTC) representation. The base `Shape` supplies strict decoding, immutable fields and generated schemas.

All read operations have effect `NONE`. Submitting values or a definition has effect `DATA`. Making a workspace view has effect `DOCUMENT`. Each operation checks its specific permission and the resource scope in section 8.17. Approval decisions are HTTP-only through the section 3 route.

| Function and operation namespace | HTTP route | Request | Response | Effect |
|---|---|---|---|---|
| cube.list_cube_parameters | GET /api/cube/parameters | CubeCatalogInput | CubeDictionary | NONE |
| cube.list_cube_missions | GET /api/cube/missions | CubeMissionList | CubeMissionPage | NONE |
| cube.find_cube_missions | POST /api/cube/find | CubeQuery | CubeSearch | NONE |
| cube.get_cube_mission | GET /api/cube/missions/:missionId | CubeMissionInput | CubeMission | NONE |
| cube.compare_cube_missions | POST /api/cube/compare | CubeCompareInput | CubeComparison | NONE |
| cube.similar_cube_missions | POST /api/cube/similar | CubeSimilarInput | CubeSimilarPage | NONE |
| cube.explain_cube_distance | POST /api/cube/distance | CubeDistanceInput | CubeDistance | NONE |
| cube.get_cube_value_evidence | GET /api/cube/values/:valueId | CubeValueInput | CubeEvidence | NONE |
| cube.search_cube_documents | POST /api/cube/documents/search | CubeDocumentQuery | CubeDocumentPage | NONE |
| cube.export_cube_view | POST /api/cube/views | CubeExportInput | WorkspaceFile | DOCUMENT |
| cube.submit_cube_values | POST /api/cube/values/candidates | CubeCandidateInput | CubeReceipt | DATA |
| cube.list_cube_approvals | GET /api/cube/approvals | CubeApprovalInput | CubeApprovalPage | NONE |
| cube.submit_cube_definition | POST /api/cube/definitions | CubeDefinitionInput | CubeReceipt | DATA |

GET path fields are supplied by the route. A structured GET query field such as `scope` is percent-encoded JSON and decoded into its strict Shape. Other GET fields are ordinary query scalars.

The following declarations extend the API Contract. `Citation`, `WorkspaceFile` and `Approval` are its existing types. Each list is bounded; compact and detailed modes change evidence detail, never access or calculated values.

```python
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID
from msgspec import Meta
from app.infrastructure.shapes import Shape
from app.documents.handlers import Citation
from app.conversations.handlers import WorkspaceFile
from app.approvals.handlers import Approval

Limit = Annotated[int, Meta(ge=1, le=20)]
CatalogLimit = Annotated[int, Meta(ge=1, le=100)]
Key = Annotated[str, Meta(min_length=1, max_length=100)]
QueryText = Annotated[str, Meta(min_length=1, max_length=500)]
Mode = Literal['compact', 'detailed']
DecimalText = Annotated[str, Meta(min_length=1, max_length=100, pattern=r'^-?(0|[1-9][0-9]*)(\.[0-9]+)?$')]

class CubeScope(Shape):
    silo_id: Key
    stage_id: Key
    class_id: Key = 'system'
    option_id: UUID | None = None
    simulated: bool = True
    valid_at: datetime | None = None
    known_at: datetime | None = None
    mode: Mode = 'compact'

class CubeResolvedScope(Shape):
    silo_id: Key
    stage_id: Key
    class_id: Key
    option_id: UUID | None
    simulated: bool
    valid_at: datetime
    known_at: datetime
    mode: Mode

class CubeCatalogInput(Shape):
    category: Key | None = None
    limit: CatalogLimit = 100

class CubeUnit(Shape):
    id: Key
    dimension: Key
    multiplier: DecimalText
    offset_value: DecimalText

class CubeParameter(Shape):
    id: Key
    label: str
    dimension: Key
    unit_id: Key
    scale: Literal['linear','log','ordinal','nominal']
    minimum: DecimalText | None
    maximum: DecimalText | None
    synonyms: list[str]

class CubeDictionary(Shape):
    parameters: list[CubeParameter]
    units: list[CubeUnit]
    class_ids: list[str]
    silo_ids: list[str]
    stage_ids: list[str]
    truncated: bool

class CubeMissionList(Shape):
    simulated: bool = True
    limit: Limit = 20

class CubeMissionSummary(Shape):
    id: UUID
    key: Key
    title: str
    simulated: bool
    created_at: datetime

class CubeMissionPage(Shape):
    items: list[CubeMissionSummary]
    returned: int
    truncated: bool

class CubePredicate(Shape):
    parameter_id: Key
    operator: Literal['eq','ge','le','between']
    value: Key
    unit_id: Key
    upper_value: DecimalText | None = None

class CubeQuery(Shape):
    scope: CubeScope
    predicates: Annotated[list[CubePredicate], Meta(min_length=1,max_length=12)]
    mission_ids: Annotated[list[UUID], Meta(max_length=20)] = []
    limit: Limit = 20

class CubeCell(Shape):
    value_id: UUID
    mission_id: UUID
    element_id: UUID
    parameter_id: Key
    unit_id: Key
    numeric_value: DecimalText | None
    text_value: str | None
    lower_bound: DecimalText | None
    upper_bound: DecimalText | None
    marker: Literal['exact','range','lower','upper','tbd','not-applicable']
    basis: Literal['observed','assumed','imputed','derived']
    confidence: float
    simulated: bool
    evidence_handle: UUID

class CubeMatch(Shape):
    mission: CubeMissionSummary
    cells: list[CubeCell]
    gaps: list[str]

class CubeSearch(Shape):
    items: list[CubeMatch]
    scope: CubeResolvedScope
    returned: int
    truncated: bool

class CubeMissionInput(Shape):
    mission_id: UUID
    scope: CubeScope

class CubeMission(Shape):
    mission: CubeMissionSummary
    scope: CubeResolvedScope
    cells: list[CubeCell]
    document_ids: list[UUID]
    gaps: list[str]
    truncated: bool

class CubeCompareInput(Shape):
    mission_ids: Annotated[list[UUID], Meta(min_length=2,max_length=5)]
    parameter_ids: Annotated[list[Key], Meta(min_length=1,max_length=12)]
    scope: CubeScope

class CubeDelta(Shape):
    left_value_id: UUID
    right_value_id: UUID
    parameter_id: Key
    delta: DecimalText
    unit_id: Key

class CubeComparison(Shape):
    missions: list[CubeMatch]
    deltas: list[CubeDelta]
    scope: CubeResolvedScope
    truncated: bool

class CubeSimilarInput(Shape):
    mission_id: UUID
    definition_id: UUID
    scope: CubeScope
    limit: Limit = 5

class CubeDistanceInput(Shape):
    left_mission_id: UUID
    right_mission_id: UUID
    definition_id: UUID
    scope: CubeScope

class CubeContribution(Shape):
    parameter_id: Key
    left_value_id: UUID
    right_value_id: UUID
    weight: float
    sigma: DecimalText
    squared: float
    share: float

class CubeDistance(Shape):
    left_mission_id: UUID
    right_mission_id: UUID
    definition_id: UUID
    definition_version: int
    comparable: bool
    distance: float | None
    coverage: float
    contributions: list[CubeContribution]
    skipped: list[str]
    scope: CubeResolvedScope
    truncated: bool

class CubeSimilarPage(Shape):
    items: list[CubeDistance]
    returned: int
    truncated: bool

class CubeValueInput(Shape):
    value_id: UUID

class CubeEvidence(Shape):
    cell: CubeCell
    citation: Citation
    recipe: str
    valid_from: datetime
    valid_to: datetime | None
    recorded_at: datetime
    input_ids: list[UUID]
    truncated: bool

class CubeDocumentQuery(Shape):
    mission_id: UUID
    scope: CubeScope
    query: QueryText
    limit: Annotated[int, Meta(ge=1,le=8)] = 8

class CubeDocumentPage(Shape):
    citations: list[Citation]
    scores: list[float]
    scope: CubeResolvedScope
    truncated: bool

class CubeExportInput(Shape):
    conversation_id: UUID
    request_id: UUID
    query: CubeQuery

class CubeCandidate(Shape):
    element_id: UUID
    parameter_id: Key
    document_id: UUID
    version: str
    locator: Key
    source_unit_id: Key
    numeric_value: DecimalText | None
    text_value: str | None
    lower_bound: DecimalText | None
    upper_bound: DecimalText | None
    marker: Literal['exact','range','lower','upper','tbd','not-applicable']
    basis: Literal['observed','assumed']
    confidence: float
    recipe: Key
    valid_from: datetime
    valid_to: datetime | None

class CubeCandidateInput(Shape):
    mission_id: UUID
    scope: CubeScope
    conversation_id: UUID
    request_id: UUID
    candidates: Annotated[list[CubeCandidate], Meta(min_length=1,max_length=100)]

class CubeReceipt(Shape):
    approval_request_id: UUID
    status: Literal['pending','executed']
    value_ids: list[UUID]
    definition_id: UUID | None

class CubeApprovalInput(Shape):
    conversation_id: UUID
    status: Literal['pending','approved','rejected','executed','failed'] = 'pending'

class CubeApprovalPage(Shape):
    items: list[Approval]
    truncated: bool

class CubeDimension(Shape):
    parameter_id: Key
    weight: float
    sigma: DecimalText

class CubeDefinitionInput(Shape):
    key: Key
    version: Annotated[int, Meta(ge=1)]
    conversation_id: UUID
    request_id: UUID
    simulated: bool
    reference_value_ids: Annotated[list[UUID], Meta(min_length=2,max_length=100)]
    dimensions: Annotated[list[CubeDimension], Meta(min_length=1,max_length=12)]
    minimum_coverage: float
    cutoff: float
    result_limit: Limit = 5
```

Numbers that must preserve source precision are decimal strings of at most 100 characters on the wire. Numeric predicates use this encoding after unit validation; nominal equality predicates use a bounded category string. Resolved output scopes carry every default and both non-null UTC times. Distances, weights, shares and confidence are finite JSON numbers. Semantic validation rejects nonfinite values, duplicate keys, duplicate mission identifiers, inappropriate units, non-UTC times, a range without its upper endpoint, and invalid markers. Confidence and coverage lie in the closed unit interval. Weights sum to one. Definition scales are recomputed from the readable reference handles before approval; supplied scales must match.

TypeScript forms and complete synthetic wire examples are in API Contract sections 13 to 15. Python defaults apply to optional input fields. Every output field is present, including nulls.

Every route uses the contract error body `{code, message, requestId, fields?}`. Invalid keys, units, operators, times and encodings use `400 invalid_input` with safe field paths. Missing authentication uses `401 not_signed_in`. Missing operation permission uses `403 forbidden`. Missing or unreadable resources, including definitions, use `404 not_found`. A stale definition version, reused request identifier with changed arguments, or repeated decision uses `409 conflict`. An oversized payload uses `413 too_large`; a rate limit uses `429 rate_limited`. Unexpected failures use `500 internal_error`. The tool gate uses its existing `approval_required` result to initiate the section 3 interruption. No new public error code is needed.

A read is idempotent for its fixed time and definition inputs. It may use POST for a structured search body. An effectful request has a request identifier unique within its conversation and operation. A matching repeat returns the existing receipt. A changed body with that identifier conflicts. Committed values and the execution event share one short transaction. Rejected requests commit no values. Retrying after rejection creates a new request.

Requests are bounded by the contract's one-megabyte body ceiling. A query has at most 12 predicates, a comparison 2 to 5 distinct missions, an intake batch 100 candidates, and a definition 100 reference handles. A dictionary returns 100 rows; other lists return at most 20 unless the existing approval list limit of 100 applies. A detailed mission view contains at most 100 cells and 100 document links. Follow-up cell requests fetch further evidence by handle. A full export or report is deferred.

## 8.14 Tools for the agent

The Model Context Protocol (MCP) server in section 3 exposes the operations in section 8.13 as tools. Tool names are the function names, without dots. The operation namespace is `cube`. Descriptions, strict input schemas and output schemas come from the operation declarations. Reference data is reached through `list_cube_parameters` and the definition operations. Separate MCP resources are deferred until a second consumer needs them; a resource must use the same access pipeline.

| Tool | Title | Description and short example |
|---|---|---|
| list_cube_parameters | Parameter dictionary | Use before filling a numeric filter. Returns keys, units and bounds. Do not guess a key. Example: list the mass category. |
| list_cube_missions | Mission list | Use to resolve a mission name to a readable identifier. It does not search prose. Example: list simulated missions. |
| find_cube_missions | Parameter search | Use for numeric conditions with known keys and units. Returns matching missions and evidence handles. Use document search for narrative conditions. Example: mass at most 115 kg. |
| get_cube_mission | Mission facts | Use after resolving a mission. Returns scoped cells, gaps and document links. Do not treat a gap as zero. Example: engineering facts at concept. |
| compare_cube_missions | Mission comparison | Use for the same parameters across 2 to 5 missions. Returns cells and operation-computed differences. Do not compare unlike scopes. Example: compare mass and power. |
| similar_cube_missions | Similar missions | Use for analogues under a known definition. Returns distances, coverage and handles. It produces no cost estimate. Example: find technical analogues to Alpha. |
| explain_cube_distance | Distance evidence | Use to explain a returned ranking. Returns contributions and both value handles. Do not infer a cause from an embedding. Example: explain Alpha and Beta. |
| get_cube_value_evidence | Value evidence | Use before stating or checking a number. Returns its source, locator and recipe. Do not use an inaccessible handle. Example: open the returned mass handle. |
| search_cube_documents | Mission documents | Use for mission prose or to cross-check a value. Returns existing document citations. It does not write a document. Example: search for thermal margin. |
| export_cube_view | Save view | Use only when a person asks to save the bounded JSON view in a conversation. It requires approval and returns a workspace file. It does not send a report outside CRAFT. |
| submit_cube_values | Record candidate values | Use when asked to record evidence-backed candidates. Shows the exact values for approval. It records observations without editing originals. Example: record a cited table cell. |
| list_cube_approvals | Cube approvals | Use to locate an owned cube request. Returns status, not decision authority. Example: list pending cube requests in this conversation. |
| submit_cube_definition | Similarity definition | Use to record an explicitly requested new definition version. Returns an approval receipt. Never recalibrate a definition silently. Example: record an explicit weight set. |

For every tool, `inputSchema` is the request declaration and `outputSchema` is the response declaration in section 8.13. Successful results contain that object in `structuredContent` and one text summary, such as `2 matching missions` or `Distance 1; coverage 1`. The summary does not add facts absent from the object.

Read tools declare `readOnlyHint=true`, `destructiveHint=false`, `idempotentHint=true` and `openWorldHint=false`. The three effectful tools declare `readOnlyHint=false`, `destructiveHint=false`, `idempotentHint=true` and `openWorldHint=false`. Their idempotency requires the request identifier. These annotations describe behavior; they grant no authority.

Compact results are at most 16 KiB. Detailed results are at most 64 KiB. A result is truncated at whole-item boundaries before encoding. The result states `truncated` and retains value, mission, document and definition handles for follow-up calls. A single evidence result whose source excerpt exceeds the limit carries a bounded excerpt and locator. No continuation handle confers permission; each follow-up rechecks it.

A tool error has `isError=true`, the safe contract code, a field path when appropriate, and a corrective instruction. An unknown key says to call the dictionary. An invalid unit says to use a unit with the parameter's dimension. An unreadable handle says to choose a returned readable handle. A noncomparable pair is a successful result with `comparable=false`, not a transport error. Tool failures cannot reveal a hidden object's existence.

For a write, the MCP gate captures the arguments, their hash, evidence and reason before the operation writes. The run saves its state and pauses. The requester decides in CRAFT using the HTTP decision route. Resume reads the database decision and restores the run state. The gate checks the original operation, arguments hash and approval identity. One execution event permits one commit. The agent never decides.

## 8.15 Agent use

The agent resolves names and dictionary keys before selecting a cube tool. It holds the returned scope and definition version for follow-up calls. Retrieved text is evidence rather than instructions. A number must equal a returned cell value or operation-computed result. Arithmetic is performed by operations and returns its input handles. The agent does not calculate from remembered figures.

| Task | Tool plan |
|---|---|
| Find and explain a set | Dictionary; parameter search; comparison; evidence for each stated figure |
| Explain similarity | Similar missions; distance explanation; both source values; document citations |
| Look up a value | Mission list; scoped mission; value evidence |
| Check disagreement | Value evidence; mission document search; display both sources and their times |
| Record a candidate | Validate source and scope; submit candidates; show the approval request; wait for the decision |

An ambiguous mission, unit, scope or date requires a clarification from the person. An unknown value is shown as missing. A disagreement is not resolved by choosing the higher-confidence source without explanation. Section 4's confidence gate limits drafting when the evidence is insufficient. Section 3 supplies time, token and tool budgets. Search retries stop at its stated limit. A refusal returns useful readable evidence and the missing requirement.

## 8.16 Intake

Originals arrive through registered document repositories. An upload belongs to its conversation under section 7; recording values from it requires an immutable section 7 document reference before the conversation can be archived. A typed form is an immutable source submission with a content hash and field locators. It is registered through the document intake boundary. It is never a source-free mission change.

The first release reads simple workbook tables and typed forms deterministically. Table cells feed values; other readable files feed document search. Later intake adds narrative extraction and scanned tables after format-specific quality gates. Source repository integration and real data require the section 12 onboarding gate.

Workbook intake reads package XML and literal cells. It does not evaluate formulas, macros, scripts or external links. Formula cells are unresolved, even if a cache exists. A conversion performed outside CRAFT can supply an approved static table with its own hash and provenance. Binary office formats need an approved conversion; proprietary project files need a static export. Archived files are inspected with depth, count and expanded-size limits, and paths cannot escape the intake directory.

| Source | Locator |
|---|---|
| Workbook | sheet name and cell range |
| Word document | heading path, paragraph ordinal and table row/column |
| Portable Document Format (PDF) | page, table and cell or bounding box |
| Markdown or HTML | heading path and block ordinal |
| Comma-separated values (CSV) | row ordinal and column key |
| JSON | JSON pointer |
| Typed form | immutable submission identifier and field path |

The header map recognizes approved bilingual synonyms for parameter, unit, value, mass, power, volume, element, phase and date. It normalizes case, whitespace and accents without translating an arbitrary cell into a parameter. Duplicate header matches, missing dimensions and mixed units are errors. Decimal separators and thousand separators follow the recipe's declared locale. A unit scale such as kW is an explicit conversion. Bounds and to-be-determined markers use the dictionary rules in section 8.6.

Intake resolves the mission key and aliases, then element identity within its mission, silo and option. A changed file hash is a new evidence version. Repeating the same hash, locator, parameter, scope and recipe is idempotent. A changed recipe creates new candidates and never rewrites old values. Version matching of entire silo documents is later work.

Candidate quantities use `sourceUnitId`; execution converts them to canonical units using the recorded recipe. The source values remain in the approved argument record.

Candidates live in the existing approval request's typed arguments and evidence. There is no new staging table. A batch contains at most 100 candidates and is accepted or rejected as a whole. The card shows source values, canonical values, unit conversion, markers, old cell handles, new valid dates, recipe, confidence, gaps and conflicts. Correcting a rejected batch creates a new request and hash.

Narrative intake later lets the model suggest dictionary keys and cited spans. A deterministic validator checks the document hash, span, unit, numeric encoding, allowed keys and scope. A model statement without a matching source span cannot become an observed value. Scanned input requires explicit optical character recognition and a page locator. Confidence calibration and false extraction measures belong to Dependencies and Integration.

Intake failures name a safe reason: unsupported representation, ambiguous identity, unknown header, unknown unit, formula cell, missing locator, conflicting label, unreadable evidence, invalid bounds, low extraction confidence, stale request or oversized batch. Observation records counts, durations and safe error codes. It records no source text, candidate payload, prompt or answer.

## 8.17 Access and approval

Operation permissions distinguish cube reads, candidate writes and definition writes. Account roles load with the trusted caller context. Agent runs receive tool permissions rather than account roles. Nested operations pass the same caller and resource scope through the handler pipeline.

A readable value requires a mission read grant, a silo read grant, readable evidence, an allowed classification and the correct simulation set. Derived values require all input permissions as well. Mission and silo access constrain state selection. The selected logical cell is then intersected with evidence and input permissions before filters, statistics, comparison, ranking and counts. An unreadable selected cell never exposes an older value. A document link does not weaken the section 9 checks. Grant revocation is a new event; the latest event determines access on the next call.

Every datacube change uses an approval request. The requester may approve or reject the complete request. The agent cannot approve. The server rechecks permissions and evidence when executing, so approval cannot preserve a revoked grant. A direct browser write shows the same confirmation and uses the same durable request and exact arguments hash. A changed body, reused execution, stale definition or rejected request cannot write.

Approval creates source records in CRAFT, not edits to original engineering documents. Section 3 owns pause, rejection, resume, replay protection and decision events. Section 7 owns their append-only storage. More approval roles and refusal of requester approval are the deferred extension in section 12.

## 8.18 Schema

The following Structured Query Language (SQL) defines the first release in migrations 0009, 0010 and 0011. Accounts, documents and original references come from section 7. The datacube migrations use their existing identifiers and original content hashes.

Reference tables define the axes. Composite keys keep mission, option, silo and document contexts together. Values, grant events and definition versions are appended. `craft_app` receives SELECT and INSERT on record tables, without UPDATE or DELETE. Access grants are administered through the owner boundary. The service enforces approval and semantic validation before insertion. Row policies also constrain readable values and links and authorized value insertion.

Dictionary changes append replacement records with new keys when meaning changes. Stable axis rows cannot be deleted while referenced. A sealed definition admits no new dimensions or reference members. Every numeric value preserves source precision. An approval request and logical-cell uniqueness constraint prevent repeated insertion for the same request.

```sql
-- 0009: axes, dictionary and access reference data
create table silos (id text primary key, label text not null);
create table stages (id text primary key, label text not null, seq integer not null unique);
create table element_classes (id text primary key, label text not null);
create table units (
 id text primary key, dimension text not null, multiplier numeric not null check(multiplier > 0),
 offset_value numeric not null default 0, unique(id,dimension));
create table parameters (
 id text primary key, label text not null, dimension text not null,
 unit_id text not null, scale text not null check(scale in ('linear','log','ordinal','nominal')),
 minimum numeric, maximum numeric, synonyms jsonb not null default '[]',
 foreign key(unit_id,dimension) references units(id,dimension),
 check(minimum is null or maximum is null or minimum <= maximum));
create table parameter_synonyms (
 parameter_id text references parameters, synonym text not null unique, recorded_at timestamptz not null default now(), primary key(parameter_id,synonym));
create table parameter_categories (
 parameter_id text references parameters, category text not null, primary key(parameter_id,category));
create table missions (
 id uuid primary key default uuidv7(), key text not null unique, title text not null,
 simulated boolean not null, created_at timestamptz not null default now());
create table mission_aliases (mission_id uuid references missions, alias text not null unique, primary key(mission_id,alias));
create table mission_silos (
 mission_id uuid references missions, silo_id text references silos, primary key(mission_id,silo_id));
create table mission_grants (
 id uuid primary key default uuidv7(), mission_id uuid not null references missions,
 account_id uuid not null references users, permission text not null check(permission in ('read','write')),
 granted boolean not null, recorded_at timestamptz not null default now());
create table silo_grants (
 id uuid primary key default uuidv7(), mission_id uuid not null, silo_id text not null,
 account_id uuid not null references users, permission text not null check(permission in ('read','write')),
 granted boolean not null, recorded_at timestamptz not null default now(),
 foreign key(mission_id,silo_id) references mission_silos);
create function can_mission(mid uuid, perm text default 'read') returns boolean
language sql stable as $$
 select coalesce((select granted from mission_grants
 where mission_id=mid and permission=perm and account_id=nullif(current_setting('craft.account_id',true),'')::uuid
 order by recorded_at desc,id desc limit 1),false)
$$;
create function can_silo(mid uuid, sid text, perm text default 'read') returns boolean
language sql stable as $$
 select can_mission(mid,perm) and coalesce((select granted from silo_grants
 where mission_id=mid and silo_id=sid and permission=perm
 and account_id=nullif(current_setting('craft.account_id',true),'')::uuid
 order by recorded_at desc,id desc limit 1),false)
$$;
create function can_document(did uuid) returns boolean language sql stable as $$
 select exists(select 1 from documents d where d.id=did and
 (d.required_role is null or exists(select 1 from user_roles r where r.role=d.required_role
 and r.user_id=nullif(current_setting('craft.account_id',true),'')::uuid)))
$$;
create table mission_options (
 id uuid primary key default uuidv7(), mission_id uuid not null references missions,
 key text not null, label text not null, unique(mission_id,id),unique(mission_id,key));
create table elements (
 id uuid primary key default uuidv7(), mission_id uuid not null, silo_id text not null,
 option_id uuid, parent_id uuid, class_id text not null references element_classes, key text not null,
 unique(mission_id,silo_id,id),unique(mission_id,silo_id,key),
 foreign key(mission_id,silo_id) references mission_silos,
 foreign key(mission_id,option_id) references mission_options(mission_id,id),
 foreign key(mission_id,silo_id,parent_id) references elements(mission_id,silo_id,id));
create index on mission_grants(account_id,mission_id,permission,recorded_at desc,id desc);
create index on silo_grants(account_id,mission_id,silo_id,permission,recorded_at desc,id desc);
```

```sql
-- 0010: document links and append-only evidence values
create table mission_documents (
 mission_id uuid not null, silo_id text not null, document_id uuid not null,
 stage_id text not null references stages, sha256 bytea not null,
 primary key(mission_id,silo_id,stage_id,document_id,sha256),
 foreign key(mission_id,silo_id) references mission_silos,
 foreign key(document_id,sha256) references original_refs(document_id,sha256));
create table parameter_values (
 id uuid primary key default uuidv7(), mission_id uuid not null, silo_id text not null,
 element_id uuid not null, parameter_id text not null references parameters,
 stage_id text not null references stages, document_id uuid not null, sha256 bytea not null, locator text not null check(length(locator)>0),
 numeric_value numeric, text_value text, lower_bound numeric, upper_bound numeric,
 marker text not null check(marker in ('exact','range','lower','upper','tbd','not-applicable')),
 basis text not null check(basis in ('observed','assumed','imputed','derived')),
 confidence numeric not null check(confidence>=0 and confidence<=1), recipe text not null,
 simulated boolean not null, valid_from timestamptz not null, valid_to timestamptz,
 recorded_at timestamptz not null default now(), approval_request_id uuid not null references approval_requests deferrable initially deferred,
 foreign key(mission_id,silo_id,element_id) references elements(mission_id,silo_id,id),
 foreign key(mission_id,silo_id,stage_id,document_id,sha256) references mission_documents(mission_id,silo_id,stage_id,document_id,sha256),
 unique(approval_request_id,element_id,parameter_id,stage_id,valid_from),
 check(valid_to is null or valid_to>valid_from),
 check(lower_bound is null or upper_bound is null or lower_bound<=upper_bound),
 check(numeric_value is null or numeric_value::text not in ('NaN','Infinity','-Infinity')),
 check(lower_bound is null or lower_bound::text not in ('NaN','Infinity','-Infinity')),
 check(upper_bound is null or upper_bound::text not in ('NaN','Infinity','-Infinity')),
 check((marker='exact' and ((numeric_value is not null and text_value is null) or (numeric_value is null and text_value is not null)) and lower_bound is null and upper_bound is null)
 or (marker='range' and text_value is null and numeric_value is null and lower_bound is not null and upper_bound is not null)
 or (marker='lower' and text_value is null and numeric_value is null and lower_bound is not null and upper_bound is null)
 or (marker='upper' and text_value is null and numeric_value is null and lower_bound is null and upper_bound is not null)
 or (marker in ('tbd','not-applicable') and text_value is null and numeric_value is null and lower_bound is null and upper_bound is null)));
create function check_value_labels() returns trigger language plpgsql as $$
declare p parameters%rowtype; endpoint numeric;
begin
 select * into strict p from parameters where id=new.parameter_id;
 if p.scale='nominal' and new.marker not in ('exact','tbd','not-applicable') then
  raise exception 'nominal marker mismatch';
 end if;
 if new.marker='exact' and ((p.scale='nominal' and new.text_value is null) or (p.scale<>'nominal' and new.numeric_value is null)) then
  raise exception 'parameter encoding mismatch';
 end if;
 if new.numeric_value is not null and ((p.minimum is not null and new.numeric_value<p.minimum) or (p.maximum is not null and new.numeric_value>p.maximum) or (p.scale='ordinal' and new.numeric_value<>trunc(new.numeric_value))) then
  raise exception 'parameter domain mismatch';
 end if;
 foreach endpoint in array array[new.numeric_value,new.lower_bound,new.upper_bound] loop
  if endpoint is not null and ((p.minimum is not null and endpoint<p.minimum)
   or (p.maximum is not null and endpoint>p.maximum)
   or (p.scale='ordinal' and endpoint<>trunc(endpoint))
   or (p.scale='log' and endpoint<=0)) then
   raise exception 'parameter domain mismatch';
  end if;
 end loop;
 if new.simulated is distinct from (select simulated from missions where id=new.mission_id)
 or new.simulated is distinct from (select simulated from documents where id=new.document_id) then
  raise exception 'inconsistent simulation labels';
 end if;
 return new;
end $$;
create trigger value_labels before insert on parameter_values for each row execute function check_value_labels();
create index on parameter_values(mission_id,silo_id,element_id,parameter_id,stage_id,valid_from desc,recorded_at desc,id desc);
create table evidence_events (
 id uuid primary key default uuidv7(), document_id uuid not null references documents,
 available boolean not null, recorded_at timestamptz not null default now());
create table value_inputs (
 value_id uuid references parameter_values, input_id uuid references parameter_values,
 primary key(value_id,input_id),check(value_id<>input_id));

-- The owner sees the logical-cell winner; the caller receives only a Boolean.
-- This prevents an unreadable newer source from exposing an older current value.
create function is_current_value(vid uuid, valid_at timestamptz, known_at timestamptz)
returns boolean language sql stable security definer set search_path=public,pg_temp as $$
 select exists(select 1 from parameter_values candidate
 where candidate.id=vid and can_silo(candidate.mission_id,candidate.silo_id)
 and candidate.id=(select newer.id from parameter_values newer
 where (newer.mission_id,newer.silo_id,newer.element_id,newer.parameter_id,newer.stage_id)=
       (candidate.mission_id,candidate.silo_id,candidate.element_id,candidate.parameter_id,candidate.stage_id)
 and newer.valid_from<=valid_at and (newer.valid_to is null or newer.valid_to>valid_at)
 and newer.recorded_at<=known_at
 order by newer.valid_from desc,newer.recorded_at desc,newer.id desc limit 1))
$$;
revoke all on function is_current_value(uuid,timestamptz,timestamptz) from public;
grant execute on function is_current_value(uuid,timestamptz,timestamptz) to craft_app;
```

```sql
-- 0011: immutable versions of editable similarity definitions
create table similarity_definitions (
 id uuid primary key default uuidv7(), key text not null, version integer not null check(version>0),
 method text not null check(method='weighted-normalized-euclidean'), simulated boolean not null,
 minimum_coverage numeric not null check(minimum_coverage>0 and minimum_coverage<=1),
 cutoff numeric not null check(cutoff>0 and cutoff::text not in ('NaN','Infinity')),
 result_limit integer not null check(result_limit between 1 and 20),
 approval_request_id uuid not null references approval_requests deferrable initially deferred, created_at timestamptz not null default now(),unique(key,version));
create table similarity_dimensions (
 definition_id uuid references similarity_definitions, parameter_id text references parameters,
 weight numeric not null check(weight>0 and weight<=1),
 sigma numeric not null check(sigma>0 and sigma::text not in ('NaN','Infinity')),
 primary key(definition_id,parameter_id));
create table similarity_reference_members (
 definition_id uuid references similarity_definitions, value_id uuid references parameter_values,
 primary key(definition_id,value_id));
create table similarity_seals (
 definition_id uuid primary key references similarity_definitions, sealed_at timestamptz not null default now());
create function seal_definition() returns trigger language plpgsql as $$
begin
 if exists(select 1 from similarity_reference_members r join parameter_values v on v.id=r.value_id
 where r.definition_id=new.definition_id and (v.marker<>'exact' or v.numeric_value is null)) then
  raise exception 'exact numeric references required';
 end if;
 if exists(select 1 from similarity_dimensions d join parameters p on p.id=d.parameter_id
 where d.definition_id=new.definition_id and p.scale<>'linear') then
  raise exception 'linear dimensions required';
 end if;
 if exists(select 1 from similarity_dimensions d left join
  (select r.definition_id,v.parameter_id,stddev_pop(v.numeric_value) sigma
   from similarity_reference_members r join parameter_values v on v.id=r.value_id
   where v.marker='exact' and v.numeric_value is not null
   group by r.definition_id,v.parameter_id) ref using(definition_id,parameter_id)
  where d.definition_id=new.definition_id and (ref.sigma is null or ref.sigma<=0 or abs(d.sigma-ref.sigma)>0.000000001)) then
  raise exception 'reference scale mismatch';
 end if;
 if (select sum(weight) from similarity_dimensions where definition_id=new.definition_id) is distinct from 1::numeric then
  raise exception 'weights must sum to one';
 end if;
 if not exists(select 1 from similarity_reference_members where definition_id=new.definition_id) then
  raise exception 'reference set required';
 end if;
 if exists(select 1 from similarity_reference_members r join parameter_values v on v.id=r.value_id
 join similarity_definitions d on d.id=r.definition_id where d.id=new.definition_id and v.simulated<>d.simulated) then
  raise exception 'mixed reference set';
 end if;
 return new;
end $$;
create trigger seal_definition before insert on similarity_seals for each row execute function seal_definition();
create function definition_child_is_unsealed() returns trigger language plpgsql as $$
begin
 if exists(select 1 from similarity_seals where definition_id=new.definition_id) then
  raise exception 'sealed definition';
 end if;
 return new;
end $$;
create trigger immutable_dimensions before insert on similarity_dimensions for each row execute function definition_child_is_unsealed();
create trigger immutable_members before insert on similarity_reference_members for each row execute function definition_child_is_unsealed();

create function can_definition(did uuid) returns boolean
language sql stable security definer set search_path=public,pg_temp as $$
 with recursive required(value_id) as (
  select value_id from similarity_reference_members where definition_id=did
  union
  select i.input_id from value_inputs i join required r on i.value_id=r.value_id)
 select exists(select 1 from similarity_seals where definition_id=did)
 and not exists(select 1 from required r join parameter_values v on v.id=r.value_id
 where not can_silo(v.mission_id,v.silo_id) or not can_document(v.document_id)
 or not coalesce((select available from evidence_events e where e.document_id=v.document_id
 and e.recorded_at<=coalesce(nullif(current_setting('craft.known_at',true),'')::timestamptz,current_timestamp)
 order by recorded_at desc,id desc limit 1),true))
$$;
revoke all on function can_definition(uuid) from public;
grant execute on function can_definition(uuid) to craft_app;
alter table similarity_definitions enable row level security;
create policy definitions_read on similarity_definitions for select to craft_app using(can_definition(id));
create policy definitions_insert on similarity_definitions for insert to craft_app with check(true);
alter table similarity_dimensions enable row level security;
create policy dimensions_read on similarity_dimensions for select to craft_app using(can_definition(definition_id));
create policy dimensions_insert on similarity_dimensions for insert to craft_app with check(true);
alter table similarity_reference_members enable row level security;
create policy members_read on similarity_reference_members for select to craft_app using(can_definition(definition_id));
create policy members_insert on similarity_reference_members for insert to craft_app with check(true);
alter table similarity_seals enable row level security;
create policy seals_read on similarity_seals for select to craft_app using(can_definition(definition_id));
create policy seals_insert on similarity_seals for insert to craft_app with check(true);
-- Definition insertion also requires the service's definition-write permission and exact approval.

-- Service authorization runs before these defence-in-depth row policies.
-- The server sets craft.account_id from trusted Caller, never from request fields.
alter table missions enable row level security;
create policy mission_read on missions for select to craft_app using(can_mission(id));
create policy mission_insert on missions for insert to craft_app with check(false);
alter table mission_options enable row level security;
create policy options_read on mission_options for select to craft_app using(can_mission(mission_id));
alter table elements enable row level security;
create policy elements_read on elements for select to craft_app using(can_silo(mission_id,silo_id));
alter table mission_documents enable row level security;
create policy links_read on mission_documents for select to craft_app using(can_silo(mission_id,silo_id) and can_document(document_id));
alter table parameter_values enable row level security;
create policy values_read on parameter_values for select to craft_app using(can_silo(mission_id,silo_id) and can_document(document_id));
create policy values_insert on parameter_values for insert to craft_app with check(can_silo(mission_id,silo_id,'write') and can_document(document_id));
-- Access grants are owner-administered; their rows cannot be written by craft_app.
grant select on mission_grants,silo_grants to craft_app;
grant select,insert on silos,stages,element_classes,units,parameters,parameter_synonyms,parameter_categories,
 missions,mission_aliases,mission_silos,mission_options,elements,mission_documents,parameter_values,
 evidence_events,value_inputs,similarity_definitions,similarity_dimensions,similarity_reference_members,similarity_seals to craft_app;
reset role;
```

## 8.19 Pseudocode

State queries apply `is_current_value` to the readable rows before numeric conditions, then check source availability at the fixed known time. The owner function returns no hidden value or count. The pure state function below takes only the authorized mission and silo scope; evidence checks follow selection.

The query builder binds values and admits only dictionary keys and declared operators. It does not execute a displayed query string.

```python
def parametric_search(caller, query):
    scope = fixed_scope(query.scope)
    predicates = validate_and_convert_predicates(query.predicates)
    readable = authorize_missions_and_silos(caller, scope)
    state = select_state_before_evidence_fallback(caller, readable, scope.valid_at, scope.known_at)
    # Each returned row must satisfy is_current_value before its evidence is used.
    state = intersect_readable_evidence_and_inputs(caller, state, scope.known_at)
    state = remove_unavailable_evidence_after_state(state, scope.known_at)
    matches = all_predicates_on_same_element(state, predicates, scope)
    return bounded_matches(matches, query.limit, scope, include_value_handles=True)

def pair_distance(left, right, definition):
    # Definition access, simulation and sealed-state checks precede this call.
    dimensions = definition.dimensions
    assert sum(d.weight for d in dimensions.values()) == 1
    assert all(d.weight > 0 and d.sigma > 0 for d in dimensions.values())
    common = {p: d for p, d in dimensions.items()
              if exact_readable(left, p) and exact_readable(right, p)}
    coverage = sum(d.weight for d in common.values())
    if coverage < definition.minimum_coverage:
        return NotComparable(coverage=coverage, missing=set(dimensions)-set(common))
    squares = {p: d.weight * ((left[p]-right[p])/d.sigma)**2
               for p, d in common.items()}
    total = sum(squares.values())
    distance = sqrt(total/coverage)
    shares = {p: contribution/total if total else 0
              for p, contribution in squares.items()}
    return Distance(left_mission_id=left.mission_id, right_mission_id=right.mission_id,
                    distance=distance, coverage=coverage, shares=shares,
                    values=both_value_handles(left, right, common))

def similar_missions(caller, request):
    definition = readable_sealed_definition(caller, request.definition_id)
    scope = fixed_scope(request.scope)
    target = current_readable_values(caller, request.mission_id, scope)
    assert target.simulated == definition.simulated
    candidates = readable_missions(caller, scope, exclude=target.mission_id)
    results = [pair_distance(target, current_readable_values(caller, mission.id, scope), definition)
               for mission in candidates]
    eligible = [r for r in results if r.comparable and r.distance <= definition.cutoff]
    return sorted(eligible, key=lambda r: (r.distance, r.right_mission_id))[:request.limit]

def state_on_date(rows, valid_at, known_at):
    eligible = [r for r in rows if r.valid_from <= valid_at
                and (r.valid_to is None or valid_at < r.valid_to)
                and r.recorded_at <= known_at]
    groups = group_by(eligible, 'mission_id', 'silo_id', 'element_id',
                     'parameter_id', 'stage_id')
    selected = [max(group, key=lambda r:(r.valid_from,r.recorded_at,r.id))
                for group in groups.values()]
    return selected  # Unknown markers remain until filtering and presentation.

def intake(caller, source, scope, recipe):
    reference = documents.register_reference(caller, source)
    parsed = literal_cells_or_typed_fields(reference, recipe)
    candidates = validate_dictionary_units_markers_identity(parsed, scope)
    request = approvals.request(caller, operation='cube.submit_cube_values',
                                arguments=typed_batch(candidates), evidence=reference)
    return request  # No value row is inserted here.

def execute_approved_intake(caller, approval_id):
    request = approvals.load_exact_unexecuted(caller, approval_id)
    authorize_again(caller, request.arguments, request.evidence)
    with short_transaction() as session:
        claim_execution_once(session, request.id)
        values = storage.insert_values(session, request.arguments, request.id)
        append_executed_event(session, request.id)
    return values
```

## 8.20 The datacube as a data source

The datacube is a component inside CRAFT at `app/datacube/`. Its handlers are its typed data-source interface. They use the `cube` operation namespace and share the application MCP server, caller context, approvals, call log and database. The browser sees the HTTP routes in section 8.13. The agent sees their generated tools.

A separate universal data-source interface is not required for the first release. A Python Protocol is introduced when two real implementations exist, as section 6 specifies. The handler boundary already permits a later external implementation without changing the browser forms or agent tool schemas. It keeps document retrieval and parametric calculations distinct while sharing citation types and resource checks.

```python
# app/datacube/handlers.py
from app.infrastructure.operations import operation, NONE, DATA

@operation('cube.read', post='/cube/find', tool=True, effects=NONE)
def find_cube_missions(caller: Caller, request: CubeQuery) -> CubeSearch:
    """Find readable missions by numeric conditions. Use dictionary keys and units.
    For narrative questions call search_cube_documents. Example: mass <= 115 kg.
    """
    return service.find(caller, request)

@operation('cube.write', post='/cube/values/candidates', tool=True, effects=DATA)
def submit_cube_values(caller: Caller, request: CubeCandidateInput) -> CubeReceipt:
    """Record evidence-backed candidates after the requester's decision.
    Use for an explicitly requested intake. Example: record a cited mass cell.
    """
    return service.record_approved(caller, request)

# app/datacube/service.py
# Cross-component calls enter through their handlers with the same caller.
def mission_documents(caller, request):
    links = load_readable_links_in_short_transaction(caller, request.scope)
    return documents.search_documents(caller, make_document_query(request, links))
```

Parsers and similarity methods are replaceable functions within the component. Later, a standalone implementation may own a schema namespace and migration stream and expose its own MCP server. Moving persistence is an explicit adapter change, not a second store in the first release.

## 8.21 Later releases and their extension points

| Release | Capability | Existing extension point | Section 12 row |
|---|---|---|---|
| R2 Evidence intake | Narrative and scanned-table candidates; static exports of older formats; dataset batches | Document references, locator and recipe fields, typed candidate batches | Narrative and dataset intake |
| R3 Engineering history | Silo document versions, block versions, styled differences and frozen gate baselines | Mission-document links, stable elements, stages and both time axes | Revisions and version matching |
| R3 Engineering history | Category, silo and stage embeddings as another method | Parameter categories and versioned similarity definitions | Category embeddings |
| R4 Simulated finance | Earned value and one current estimate per period | Mission identifiers, dated evidence and append-only estimate selection events | Earned value and current estimate |
| R4 Simulated finance | Invoice identities and update-linked projections | Original references and mission-document version links | Invoices |
| R4 Simulated finance | Evidence-backed estimates with uncertainty and analogue lists | Similarity definitions and returned value handles | Cost estimation |
| R5 Reports | Styled export reports and complete packages | Existing workspace files, labels and export approvals | Export reports |

The reference stage list can be replaced when the actual stages, gates and deliverables are supplied. The mission and silo access model, approval policy, similarity editing, state queries, financial authority, estimate identity, repository boundary, capability traceability and original locations have their specific extension rows in section 12.
