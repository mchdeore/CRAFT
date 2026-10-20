// Explicit mock preview keeps generated conversation bodies in memory only.
import type { Conversation } from "craft-api/api/types";
export type Sessions = Record<string, Conversation>;
let current: Sessions = {};
function clearLegacyStorage() {
  try { sessionStorage.removeItem("craft-mock-sessions"); } catch { /* Storage is optional. */ }
}
export function loadSessions(): Sessions { clearLegacyStorage(); return current; }
export function saveSessions(sessions: Sessions): void { clearLegacyStorage(); current = sessions; }
