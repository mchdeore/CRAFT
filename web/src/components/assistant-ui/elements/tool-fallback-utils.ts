import type {
  ToolCallMessagePart,
  ToolCallMessagePartStatus,
} from "@assistant-ui/react";

export const formatUnknownValue = (value: unknown, space?: number): string => {
  if (typeof value === "string") return value;

  try {
    if (value instanceof Error) return String(value);

    const json = JSON.stringify(value, null, space);
    if (json !== undefined) return json;
  } catch {
    // Cycles, BigInt and throwing toJSON methods fall back to string conversion.
  }

  try {
    return String(value);
  } catch {
    return "[Unserializable value]";
  }
};

export const offersInterruptAction = (
  status: ToolCallMessagePartStatus | undefined,
  approval: ToolCallMessagePart["approval"],
  interrupt: ToolCallMessagePart["interrupt"],
) =>
  status?.type !== "requires-action" ||
  status.reason !== "interrupt" ||
  approval != null ||
  interrupt != null;
