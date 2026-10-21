import type { CitationSource } from "@/components/citations/citation-context";

// A citation is a Markdown link like [1](/api/citations/<id>). craft-sources
// only answers for IDs its tools returned, so a link it doesn't know is
// unverified: the agent never actually retrieved that passage.
const CITATION_PATH = /^\/api\/citations\/[A-Za-z0-9_-]{1,64}$/;
const CITATION_LINK = /\[([1-9]\d{0,2})\]\((\/api\/citations\/[A-Za-z0-9_-]{1,64})\)/g;

// What craft-sources returns for a citation link.
type CitationRecord = Omit<CitationSource, "number">;

type CitationState =
  | { status: "loading" }
  | { status: "verified"; record: CitationRecord }
  | { status: "unverified" }
  | { status: "error" };

interface CitationLinkRef {
  number: number;
  url: string;
}

export function isCitationURL(href: string): boolean {
  return CITATION_PATH.test(href);
}

// The citation links in a message's text, once per link, numbered as the
// answer first numbered them.
export function citationLinks(
  parts: readonly { type: string; text?: string }[],
): CitationLinkRef[] {
  const links: CitationLinkRef[] = [];
  const seen = new Set<string>();
  for (const part of parts) {
    if (part.type !== "text" || !part.text) continue;
    for (const match of part.text.matchAll(CITATION_LINK)) {
      const url = match[2]!;
      if (seen.has(url)) continue;
      seen.add(url);
      links.push({ number: Number(match[1]), url });
    }
  }
  return links;
}

// Cache entries and in-flight requests belong to the current identity only.
const settled = new Map<string, CitationState>();
const pending = new Map<string, Promise<void>>();
const controllers = new Set<AbortController>();
let generation = 0;

export function clearCitationCache(): void {
  ++generation;
  for (const controller of controllers) controller.abort();
  controllers.clear(); pending.clear(); settled.clear();
}

export function loadCitation(url: string): Promise<void> {
  const existing = pending.get(url);
  if (existing) return existing;
  const currentGeneration = generation;
  const controller = new AbortController();
  controllers.add(controller);
  const request = fetch(url, { credentials: "same-origin", signal: controller.signal })
    .then(async (response): Promise<CitationState> => {
      if (response.status === 404) return { status: "unverified" };
      if (!response.ok) return { status: "error" };
      return { status: "verified", record: (await response.json()) as CitationRecord };
    })
    .catch((): CitationState => ({ status: "error" }))
    .then((state) => {
      if (generation !== currentGeneration || controller.signal.aborted) return;
      settled.set(url, state);
      if (state.status === "error" && pending.get(url) === request) pending.delete(url);
    })
    .finally(() => { controllers.delete(controller); });
  pending.set(url, request);
  return request;
}

export function currentStates(urls: readonly string[]): Map<string, CitationState> {
  return new Map(urls.map((url) => [url, settled.get(url) ?? { status: "loading" }]));
}

export function useCitationStates(
  urls: readonly string[],
): ReadonlyMap<string, CitationState> {
  // Inline links are not verified here; checked answer evidence owns citations.
  return new Map(urls.map((url) => [url, { status: "unverified" } as CitationState]));
}
