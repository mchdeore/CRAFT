# 14. Vision

- Shared documents:
  - Markdown sections; every save is an immutable revision (the `drafts` pattern).
  - Section-level diffs between revisions, with the author of each change.
  - Small live events ("v6 saved") on the chat's Server-Sent Events (SSE) stream. No keystroke co-editing.
  - Accepting or rejecting section changes creates a new revision. A stale base returns `409`.
  - Artificial intelligence (AI) edits are candidate changes returned as a diff (`documents.propose_change`); accepting one is a person's action.
  - Export uses Pandoc and a document template to create `.docx`, recording the template version and output hash. Pandoc runs as an external tool under the GNU General Public License.
- Finance forecasting: mission cost estimates from past missions, with evidence and approval before use.
- Excel add-in: another front end using the same Hypertext Transfer Protocol (HTTP) routes.
