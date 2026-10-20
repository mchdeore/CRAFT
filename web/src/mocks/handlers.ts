// Mock backend for `npm run dev:mock`. It answers the requests the app makes,
// so the frontend runs without a CRAFT backend.
//
// Mocked routes:
//
//   POST  /api/conversations       start a conversation
//   GET   /api/conversations/:id   load a conversation with its history
//   PATCH /api/conversations/:id   rename or archive a conversation
//   POST  /api/agent/runs          ask a question; streams the answer
//   GET   /api/conversations/:id/files                  files the agent published
//   GET   /api/conversations/:id/files/:fileId/content  one published file
//   GET   /api/citations/:id       a citation link's passage (craft-sources)
//
// Request and response shapes follow the API contract (craft-api).
//
// Conversations are kept in sessionStorage, so a page reload restores them as
// a real backend would. Every answer is the same canned reply with one
// citation.
//
// Supporting code: scenario.ts (the canned answer), storage.ts (where
// conversations are kept), event-stream.ts (the answer as AG-UI events).

import type { RunAgentInput } from "@ag-ui/client";
import type {
  ApiError,
  Conversation,
  ConversationSummary,
  ConversationUpdate,
  NewConversation,
} from "craft-api/api/types";
import { bypass, http, HttpResponse, type HttpResponseResolver } from "msw";
import { eventStream, turnEvents } from "./event-stream";
import { CITATION, MOCK_OUTPUT, MOCK_OUTPUT_URL, mockTurn } from "./scenario";
import { loadSessions, saveSessions } from "./storage";

function sessionNotFound() {
  const body: ApiError = {
    code: "not_found",
    message: "Agent session was not found or has expired",
    requestId: crypto.randomUUID(),
  };
  return HttpResponse.json(body, { status: 404 });
}

function summary(conversation: Conversation): ConversationSummary {
  const { id, title, updatedAt, archived, pendingApprovals } = conversation;
  return { id, title, updatedAt, archived, pendingApprovals };
}

// ---------------------------------------------------------------------------
// POST /api/conversations
// Body: NewConversation { title? }  ->  201 ConversationSummary
// ---------------------------------------------------------------------------

const createConversation: HttpResponseResolver = async ({ request }) => {
  const { title } = (await request.json().catch(() => ({}))) as NewConversation;

  const sessions = loadSessions();
  const conversation: Conversation = {
    id: crypto.randomUUID(),
    title: title?.trim() || "New conversation",
    updatedAt: new Date().toISOString(),
    archived: false,
    pendingApprovals: 0,
    messages: [],
  };
  sessions[conversation.id] = conversation;
  saveSessions(sessions);

  return HttpResponse.json(summary(conversation), { status: 201 });
};

// ---------------------------------------------------------------------------
// GET /api/conversations/:id
// -> 200 Conversation (summary fields plus AG-UI messages), or 404
// ---------------------------------------------------------------------------

const getConversation: HttpResponseResolver<{ id: string }> = ({ params }) => {
  const conversation = loadSessions()[params.id];
  if (!conversation) return sessionNotFound();

  return HttpResponse.json(conversation);
};

// ---------------------------------------------------------------------------
// PATCH /api/conversations/:id
// Body: ConversationUpdate { title?, archived? }  ->  ConversationSummary
// Archiving ends the conversation, as the real backend does.
// ---------------------------------------------------------------------------

const updateConversation: HttpResponseResolver<{ id: string }> = async ({
  params,
  request,
}) => {
  const update = (await request.json().catch(() => ({}))) as ConversationUpdate;
  const sessions = loadSessions();
  const conversation = sessions[params.id];
  if (!conversation) return sessionNotFound();

  if (update.title?.trim()) conversation.title = update.title.trim();
  if (update.archived) {
    delete sessions[params.id];
    saveSessions(sessions);
    return HttpResponse.json({ ...summary(conversation), archived: true });
  }

  conversation.updatedAt = new Date().toISOString();
  saveSessions(sessions);
  return HttpResponse.json(summary(conversation));
};

// ---------------------------------------------------------------------------
// POST /api/agent/runs
// Body: AG-UI RunAgentInput  ->  stream of AG-UI events (server-sent events)
// Records the turn in the conversation, then streams it as events.
// ---------------------------------------------------------------------------

const runAgent: HttpResponseResolver = async ({ request }) => {
  const input = (await request.json()) as RunAgentInput;
  const sessions = loadSessions();
  const conversation = sessions[input.threadId];
  if (!conversation) return sessionNotFound();

  const last = input.messages.at(-1);
  const question =
    last?.role === "user" && typeof last.content === "string" ? last.content : "";
  const turn = mockTurn(question);

  conversation.messages.push(
    turn.userMessage,
    turn.toolCallMessage,
    turn.toolResultMessage,
    turn.answerMessage,
  );
  conversation.updatedAt = new Date().toISOString();
  saveSessions(sessions);

  return new HttpResponse(eventStream(turnEvents(input, turn)), {
    headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" },
  });
};

// ---------------------------------------------------------------------------
// GET /api/conversations/:id/files  ->  WorkspaceFile[]
// The canned answer "publishes" one file, so it is listed once the
// conversation has an answer.
// ---------------------------------------------------------------------------

const listFiles: HttpResponseResolver<{ id: string }> = ({ params }) => {
  const conversation = loadSessions()[params.id];
  if (!conversation) return sessionNotFound();
  const answered = conversation.messages.some((message) => message.role === "assistant");
  return HttpResponse.json(answered ? [{ ...MOCK_OUTPUT, conversationId: params.id }] : []);
};

// ---------------------------------------------------------------------------
// GET /api/conversations/:id/files/:fileId/content  ->  the file
// ---------------------------------------------------------------------------

const getFileContent: HttpResponseResolver<{ id: string; fileId: string }> = ({ params }) => {
  if (!loadSessions()[params.id]) return sessionNotFound();
  // Route params arrive decoded; the file's ID is URL-encoded.
  if (params.fileId !== decodeURIComponent(MOCK_OUTPUT.id)) {
    const body: ApiError = { code: "not_found", message: "File not found", requestId: crypto.randomUUID() };
    return HttpResponse.json(body, { status: 404 });
  }
  return fetch(bypass(new Request(new URL(MOCK_OUTPUT_URL, location.origin))));
};

// ---------------------------------------------------------------------------
// GET /api/citations/:id  (craft-sources, not the backend)
// ->  200 the citation's passage, or 404 when the link is unverified. Only the
// canned citation exists.
// ---------------------------------------------------------------------------

const getCitation: HttpResponseResolver<{ id: string }> = ({ params }) =>
  params.id === CITATION.id
    ? HttpResponse.json(CITATION)
    : HttpResponse.json({ error: "Citation not found" }, { status: 404 });

// ---------------------------------------------------------------------------
// Route table
// ---------------------------------------------------------------------------

export const handlers = [
  http.post("/api/conversations", createConversation),
  http.get("/api/conversations/:id", getConversation),
  http.patch("/api/conversations/:id", updateConversation),
  http.post("/api/agent/runs", runAgent),
  http.get("/api/conversations/:id/files", listFiles),
  http.get("/api/conversations/:id/files/:fileId/content", getFileContent),
  http.get("/api/citations/:id", getCitation),
];
