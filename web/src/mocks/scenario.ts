// The canned answer every mock run gives: one search tool call, a short
// answer and one citation.

import type { WorkspaceFile } from "craft-api/api/types";
import type {
  AssistantMessage,
  ToolMessage,
  UserMessage,
} from "@ag-ui/client";

const SOURCE_ID = "src_0123456789abcdef01234567";
const VERSION = "sha256-mock0000000000000000000000000000000000000000000000000000000";
const PREVIEW_URL = `/api/documents/${SOURCE_ID}/preview?version=${VERSION}&artifact=main.md&locator=h%3Amission-overview`;

// The one citation every answer carries, as GET /api/citations/:id returns it.
export const CITATION = {
  id: "mockLumen001",
  url: "/api/citations/mockLumen001",
  simulated: true,
  title: "Astronomy - LUMEN Observatory - Part A.docx",
  mediaType:
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  sourceID: SOURCE_ID,
  version: VERSION,
  artifactID: "main.md",
  locator: "h:mission-overview",
  locationLabel: "§ Mission Overview",
  previewURL: PREVIEW_URL,
  downloadURL: `/api/documents/${SOURCE_ID}/download?version=${VERSION}`,
};

// The file the canned answer "publishes", served from public/mock-files/.
export const MOCK_OUTPUT_URL = "/mock-files/Sample Costing Plan (DRAFT).docx";
export const MOCK_OUTPUT: Omit<WorkspaceFile, "conversationId"> = {
  id: "Sample%20Costing%20Plan%20(DRAFT).docx",
  name: "Sample Costing Plan (DRAFT).docx",
  mediaType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  sizeBytes: 60_524,
  origin: "agent",
  state: "ready",
  classification: "unclassified",
  simulated: true,
  createdAt: "2026-10-15T12:00:00.000Z",
};

// The answer links [1] to the citation's URL, so the link opens it.
function answerText(question: string): string {
  return [
    `This is a mock answer to: "${question}"`,
    "",
    `The LUMEN Observatory is a space telescope for finding and characterizing exoplanets from Sun–Earth L2 [1](${CITATION.url}).`,
    "",
    "Run the app with a CRAFT backend to get real answers.",
  ].join("\n");
}

export type MockTurn = ReturnType<typeof mockTurn>;

// One question and its answer, as the AG-UI messages the history route returns:
// the question, a search tool call, the tool's result and the answer.
export function mockTurn(question: string) {
  const now = Date.now();
  const toolCallId = `call-${now}`;

  // `satisfies` keeps the plain-text type, which fits both the client's AG-UI
  // version and the one the API contract uses.
  const userMessage = {
    id: `user-${now}`,
    role: "user",
    content: question,
  } satisfies UserMessage;
  const toolCallMessage: AssistantMessage = {
    id: `assistant-${now}`,
    role: "assistant",
    toolCalls: [
      {
        id: toolCallId,
        type: "function",
        function: { name: "grep_documents", arguments: JSON.stringify({ query: question }) },
      },
    ],
  };
  const toolResultMessage: ToolMessage = {
    id: `tool-${toolCallId}`,
    role: "tool",
    toolCallId,
    content: `Found 1 passage in "${CITATION.title}" (${CITATION.locationLabel}).`,
  };
  const answerMessage: AssistantMessage = {
    id: `assistant-${now + 1}`,
    role: "assistant",
    content: answerText(question),
  };

  return { userMessage, toolCallMessage, toolResultMessage, answerMessage };
}
