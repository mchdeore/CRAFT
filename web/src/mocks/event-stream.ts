// Turns a mock turn into the AG-UI events a real backend would stream.

import { EventType, type AGUIEvent, type RunAgentInput } from "@ag-ui/client";
import { delay } from "msw";
import type { MockTurn } from "./scenario";

// The same turn as the live stream of AG-UI events, in the order the real
// backend sends them.
export function turnEvents(input: RunAgentInput, turn: MockTurn): AGUIEvent[] {
  const toolCall = turn.toolCallMessage.toolCalls![0]!;
  const answer = turn.answerMessage.content ?? "";
  const textId = `${turn.answerMessage.id}-text-0`;

  return [
    { type: EventType.RUN_STARTED, threadId: input.threadId, runId: input.runId },

    // The tool call and its result.
    {
      type: EventType.TOOL_CALL_START,
      toolCallId: toolCall.id,
      toolCallName: toolCall.function.name,
      parentMessageId: turn.toolCallMessage.id,
    },
    { type: EventType.TOOL_CALL_ARGS, toolCallId: toolCall.id, delta: toolCall.function.arguments },
    { type: EventType.TOOL_CALL_END, toolCallId: toolCall.id },
    {
      type: EventType.TOOL_CALL_RESULT,
      messageId: turn.toolResultMessage.id,
      toolCallId: toolCall.id,
      role: "tool",
      content: turn.toolResultMessage.content,
    },

    // The answer, in small chunks so it appears to stream in.
    { type: EventType.TEXT_MESSAGE_START, messageId: textId, role: "assistant" },
    ...(answer.match(/.{1,12}/gs) ?? []).map(
      (delta): AGUIEvent => ({ type: EventType.TEXT_MESSAGE_CONTENT, messageId: textId, delta }),
    ),
    { type: EventType.TEXT_MESSAGE_END, messageId: textId },

    { type: EventType.RUN_FINISHED, threadId: input.threadId, runId: input.runId },
  ];
}

// Writes events as a server-sent event stream ("data: <json>" per event),
// pausing between them like a real agent.
export function eventStream(events: AGUIEvent[]): ReadableStream {
  const encoder = new TextEncoder();
  return new ReadableStream({
    async start(controller) {
      for (const event of events) {
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
        await delay(event.type === EventType.TEXT_MESSAGE_CONTENT ? 25 : 300);
      }
      controller.close();
    },
  });
}
