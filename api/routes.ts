import type * as T from "./types";
export interface Routes {
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
}
