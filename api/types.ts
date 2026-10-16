export type Id = string;
export type Role = "member" | "admin";
export interface SignIn { username: string; password: string }
export interface Me { id: Id; username: string; displayName: string; roles: Role[] }
export interface Health { status: "ok" | "unavailable" }
export interface FieldError { path: string; message: string }
export interface ApiError { code: string; message: string; requestId: string; fields?: FieldError[] }
export type Classification = "unclassified" | "protected-a" | "protected-b";
export interface CitationHandle { item: string; version: number; block: number; locator: string; quoteSha256: string; simulated: boolean }
export interface Tags { mission: string | null; silo: string | null; stage: string | null }
export interface LibrarySearch { query: string; missions?: string[]; silos?: string[]; stages?: string[]; kinds?: ("chunk" | "document")[]; limit?: number }
export interface ChunkText { handle: CitationHandle; headingPath: string; text: string; score?: number }
export interface LibraryHit { kind: "chunk" | "document"; handle: CitationHandle; title: string; headingPath: string; text: string; score: number; tags: Tags; classification: Classification; before: ChunkText | null; after: ChunkText | null }
export interface LibraryHits { hits: LibraryHit[]; truncated: boolean }
export interface DocumentRef { item: string; version?: number }
export interface LibraryDocument { item: string; version: number; title: string; path: string; tags: Tags; summary: string; summaryKind: "extract" | "generated"; outline: { block: number; headingPath: string }[]; versions: { version: number; sha256: string; indexedAt: string }[]; simulated: boolean; classification: Classification; deleted: boolean }
export interface ChunkRange { item: string; version: number; fromBlock: number; toBlock: number }
export interface ExploreInput { handle: CitationHandle; before?: number; after?: number; query?: string }
export interface ChunkPage { chunks: ChunkText[]; truncated: boolean }
