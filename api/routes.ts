import type * as T from "./types";
export interface Routes {
  "POST /api/cube/candidates": { body: T.PrepareValues; response: T.Candidates };
  "POST /api/library/citations/check": { body: T.CitationCheck; response: T.CitationStatus };
  "GET /api/library/documents/:item/compare": { query: { fromVersion: number; toVersion: number }; response: T.VersionDiff };
  "GET /health/live": { response: T.Health };
  "GET /health/ready": { response: T.Health };
  "POST /api/library/search": { body: T.LibrarySearch; response: T.LibraryHits };
  "GET /api/library/documents/:item": { response: T.LibraryDocument };
  "GET /api/library/documents/:item/chunks": { response: T.ChunkPage };
  "POST /api/library/explore": { body: T.ExploreInput; response: T.ChunkPage };
  "GET /api/library/documents/:item/preview": { response: Blob };
  "GET /api/library/documents/:item/download": { response: Blob };
  "POST /api/session": { body: T.SignIn; response: T.Me };
  "DELETE /api/session": { response: void };
  "GET /api/me": { response: T.Me };
  "GET /api/cube/missions": { query: {query?: string; limit?: number}; response: T.Mission[] };
  "GET /api/cube/parameters": { response: T.Parameter[] };
  "POST /api/cube/find": { body: T.FindMissions; response: T.FoundMissions };
  "GET /api/cube/missions/:key": { query: {silo?: string; stage?: string; at?: string; knownAt?: string}; response: T.MissionState };
  "POST /api/cube/compare": { body: T.CompareMissions; response: T.Comparison };
  "GET /api/cube/values/:id": { response: T.ValueEvidence };
  "POST /api/summarize": { body: T.SelectedSource; response: T.SummaryResult };
  "POST /api/summarize/upload": { body: Blob; response: T.SummaryResult };
}
