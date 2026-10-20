import {
  useCitations,
  type CitationSource,
} from "@/components/citations/citation-context";
import {
  citationLinks,
  useCitationStates,
} from "@/components/citations/citation-records";
import { useAuiState } from "@assistant-ui/react";
import { FileSpreadsheetIcon, FileTextIcon } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { answerEvidence, checkSource, citationKey, type AnswerCitation, type AnswerEvidence, type CheckedSource } from "@/lib/chat-evidence";

interface SourceGroup {
  key: string;
  title: string;
  mediaType: string;
  citations: CitationSource[];
}

export function CitationList() {
  return import.meta.env?.VITE_MOCK === "true" ? <MockCitationList /> : <CheckedCitationList />;
}

function CheckedCitationList() {
  const parts = useAuiState((state) => state.message.parts);
  const answers = useMemo(() => answerEvidence(parts), [parts]);
  const key = JSON.stringify(answers);
  return answers.length ? <CheckedSources key={key} answers={answers} /> : null;
}

function CheckedSources({ answers }: { answers: AnswerEvidence[] }) {
  const { beginSourceRequest, completeSourceRequest, cancelSourceRequest, refuseSourceRequest, refusedCitationIds } = useCitations();
  const entries = useMemo(() => {
    const unique = new Map<string, AnswerCitation>();
    for (const answer of answers) for (const citation of answer.citations) {
      if (unique.size === 8) break;
      unique.set(citationKey(citation.handle), citation);
    }
    return [...unique.values()];
  }, [answers]);
  const [records, setRecords] = useState<{ source: CheckedSource; citation: AnswerCitation }[]>([]);
  const checked = records.filter(({ source }) => !refusedCitationIds.has(source.id));
  const [loading, setLoading] = useState(true);
  const [opening, setOpening] = useState(false);
  const openRequest = useRef<ReturnType<typeof beginSourceRequest> | null>(null);
  const current = useRef(true);
  useEffect(() => {
    current.current = true;
    const controller = new AbortController();
    void Promise.all(entries.map(async (citation) => {
      const source = await checkSource(citation, controller.signal);
      return source ? { source, citation } : null;
    })).then((results) => {
      if (controller.signal.aborted) return;
      setRecords(results.filter((value) => value !== null));
      setLoading(false);
    });
    return () => { current.current = false; controller.abort(); if (openRequest.current) cancelSourceRequest(openRequest.current); };
  }, [entries, cancelSourceRequest]);

  const open = async (citation: AnswerCitation) => {
    // Claim the shared identity synchronously, before any answer awaits.
    const request = beginSourceRequest();
    openRequest.current = request;
    setOpening(true);
    const onAbort = () => { if (current.current && openRequest.current === request) setOpening(false); };
    request.signal.addEventListener("abort", onAbort, { once: true });
    const source = await checkSource(citation, request.signal);
    request.signal.removeEventListener("abort", onAbort);
    if (!current.current || request.signal.aborted) return;
    setOpening(false);
    if (source) completeSourceRequest(request, source);
    else refuseSourceRequest(request, citationKey(citation.handle));
  };
  if (!entries.length) return null;
  return (
    <section className="bg-muted/25 mt-6 rounded-xl border p-3" aria-label="Sources" aria-busy={loading || opening}>
      <div className="mb-2 flex items-baseline justify-between gap-3 px-1">
        <h3 className="text-sm font-semibold">Sources</h3>
        {checked.length > 0 && <span className="text-muted-foreground text-xs">{checked.length} checked {checked.length === 1 ? "source" : "sources"}</span>}
      </div>
      {loading && <p className="text-muted-foreground px-1 text-xs">Checking sources…</p>}
      <div className="divide-y">
        {checked.map(({ source, citation }, index) => (
          <div key={source.id} className="flex gap-3 px-1 py-3">
            <span className="bg-background flex size-8 shrink-0 items-center justify-center rounded-lg border"><FileTextIcon className="text-muted-foreground size-4" /></span>
            <div className="min-w-0 flex-1">
              <button type="button" className="hover:text-primary block max-w-full truncate text-left text-sm font-medium" onClick={() => void open(citation)}>{source.title}</button>
              <p className="text-muted-foreground mt-1 text-xs">{source.simulated ? "Simulated · " : ""}{source.classification} · version {source.version}</p>
              <p className="mt-2 whitespace-pre-wrap break-words text-xs">{source.quote}</p>
              <button type="button" className="bg-background hover:bg-accent mt-2 max-w-full truncate rounded-full border px-2 py-1 text-xs" onClick={() => void open(citation)}>[{index + 1}] {source.locationLabel}</button>
            </div>
          </div>
        ))}
      </div>
      {!loading && checked.length === 0 && <p className="text-muted-foreground px-1 text-xs">Sources are unavailable.</p>}
      {opening && <p className="text-muted-foreground px-1 text-xs">Rechecking source…</p>}
      {!loading && answers.length === 1 && checked.length > 0 && checked.length === entries.length && <p className="text-muted-foreground border-t px-1 pt-2 text-xs">Confidence: {answers[0]!.confidence.level} · {answers[0]!.confidence.reason}</p>}
    </section>
  );
}

function MockCitationList() {
  const { openSource } = useCitations();
  const messageParts = useAuiState((state) => state.message.parts);
  const links = useMemo(() => citationLinks(messageParts), [messageParts]);
  const states = useCitationStates(
    useMemo(() => links.map((link) => link.url), [links]),
  );
  const sources = links.flatMap((link): CitationSource[] => {
    const state = states.get(link.url);
    return state?.status === "verified"
      ? [{ ...state.record, number: link.number }]
      : [];
  });
  const unverified = links.filter(
    (link) => states.get(link.url)?.status === "unverified",
  );
  const groups = groupSources(sources);
  const citationCount = groups.reduce(
    (count, group) => count + group.citations.length,
    0,
  );

  if (groups.length === 0 && unverified.length === 0) return null;

  return (
    <section
      className="bg-muted/25 mt-6 rounded-xl border p-3"
      aria-label="Sources"
    >
      <div className="mb-2 flex items-baseline justify-between gap-3 px-1">
        <h3 className="text-sm font-semibold">Sources</h3>
        <span className="text-muted-foreground text-xs">
          {groups.length} {groups.length === 1 ? "document" : "documents"} ·{" "}
          {citationCount} {citationCount === 1 ? "citation" : "citations"}
        </span>
      </div>

      <div className="divide-y">
        {groups.map((group) => {
          const SourceIcon = group.mediaType.includes("spreadsheet")
            ? FileSpreadsheetIcon
            : FileTextIcon;
          return (
            <div key={group.key} className="flex gap-3 px-1 py-3 first:pt-2 last:pb-1">
              <span className="bg-background flex size-8 shrink-0 items-center justify-center rounded-lg border shadow-xs">
                <SourceIcon className="text-muted-foreground size-4" />
              </span>
              <div className="min-w-0 flex-1">
                <button
                  type="button"
                  className="hover:text-primary block max-w-full truncate text-left text-sm font-medium"
                  onClick={() => openSource(group.citations[0]!)}
                >
                  {group.title}
                </button>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {group.citations.map((citation) => (
                    <button
                      key={citation.id}
                      type="button"
                      className="bg-background hover:bg-accent focus-visible:ring-ring inline-flex max-w-full items-center gap-1.5 rounded-full border px-2 py-1 text-xs outline-none transition-colors focus-visible:ring-2"
                      onClick={() => openSource(citation)}
                      title={citation.locationLabel || citation.locator}
                    >
                      <span className="text-muted-foreground font-medium">
                        [{citation.number}]
                      </span>
                      <span className="max-w-56 truncate">
                        {cleanLocationLabel(citation)}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {unverified.length > 0 && (
        <p className="text-muted-foreground border-t px-1 pt-2 text-xs">
          Unverified:{" "}
          {unverified.map((link) => `[${link.number}]`).join(" ")}. The agent
          never retrieved these passages, so they are not shown as sources.
        </p>
      )}
    </section>
  );
}

function groupSources(sources: CitationSource[]): SourceGroup[] {
  const groups = new Map<string, SourceGroup>();
  for (const source of sources) {
    const key = `${source.sourceID}:${source.version}`;
    const group = groups.get(key);
    if (group) group.citations.push(source);
    else {
      groups.set(key, {
        key,
        title: source.title,
        mediaType: source.mediaType,
        citations: [source],
      });
    }
  }
  return [...groups.values()];
}

function cleanLocationLabel(source: CitationSource): string {
  return (source.locationLabel || source.locator).replace(/^§\s*/, "");
}
