import { apiFetch } from "@/lib/api-client";
import type { WorkspaceFile } from "craft-api/api/types";

// Files the agent published in a conversation (its /outputs folder).
export async function listWorkspaceFiles(
  conversationId: string,
): Promise<WorkspaceFile[]> {
  const response = await apiFetch(
    `/api/conversations/${encodeURIComponent(conversationId)}/files`,
  );
  if (!response.ok) {
    throw new Error(`Listing files failed (${response.status})`);
  }
  return (await response.json()) as WorkspaceFile[];
}

// A file's ID is already URL-encoded by the server.
export function workspaceFileURL(conversationId: string, fileId: string): string {
  return `/api/conversations/${encodeURIComponent(conversationId)}/files/${fileId}/content`;
}
