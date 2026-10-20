/* eslint-disable react-refresh/only-export-components */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type PropsWithChildren,
} from "react";

// Anything the preview panel can show: a cited passage or a file the agent
// published. The location fields only exist for citations, and let the
// panel jump to the cited page, heading or row.
export interface PreviewDocument {
  title: string;
  mediaType: string;
  previewURL: string;
  downloadURL: string;
  subtitle?: string;
  version?: string;
  sourceID?: string;
  artifactID?: string;
  sheetName?: string;
  locator?: string;
  locationLabel?: string;
  previewRequestId?: number;
}

// A verified citation: its number in the answer and the passage craft-sources
// returned for its link.
export interface CitationSource extends PreviewDocument {
  id: string;
  url: string;
  simulated: boolean;
  number: number;
  sourceID: string;
  version: string;
  artifactID: string;
  locator: string;
  locationLabel: string;
}

interface CitationContextValue {
  selectedSource: PreviewDocument | null;
  openSource: (source: PreviewDocument) => void;
  closeSource: () => void;
  beginSourceRequest: () => SourceRequest;
  completeSourceRequest: (request: SourceRequest, source: PreviewDocument) => boolean;
  cancelSourceRequest: (request: SourceRequest) => void;
  refuseSourceRequest: (request: SourceRequest, citationId: string) => void;
  refuseSource: (source: PreviewDocument) => void;
  refusedCitationIds: ReadonlySet<string>;
}

interface SourceRequest { id: number; signal: AbortSignal }

const CitationContext = createContext<CitationContextValue | null>(null);

export function CitationProvider({ children }: PropsWithChildren) {
  const [selectedSource, setSelectedSource] = useState<PreviewDocument | null>(
    null,
  );
  const requestId = useRef(0);
  const pending = useRef<{ id: number; controller: AbortController } | null>(null);
  const selected = useRef<PreviewDocument | null>(null);
  const [refusedCitationIds, setRefusedCitationIds] = useState<ReadonlySet<string>>(() => new Set());
  const beginSourceRequest = useCallback((): SourceRequest => {
    pending.current?.controller.abort();
    const request = { id: ++requestId.current, controller: new AbortController() };
    pending.current = request;
    selected.current = null;
    setSelectedSource(null);
    return { id: request.id, signal: request.controller.signal };
  }, []);
  const completeSourceRequest = useCallback((request: SourceRequest, source: PreviewDocument) => {
    if (request.signal.aborted || pending.current?.id !== request.id) return false;
    const next = { ...source, previewRequestId: request.id };
    selected.current = next;
    setSelectedSource(next);
    return true;
  }, []);
  const closeSource = useCallback(() => {
    pending.current?.controller.abort();
    pending.current = null;
    selected.current = null;
    setSelectedSource(null);
  }, []);
  const cancelSourceRequest = useCallback((request: SourceRequest) => {
    if (pending.current?.id === request.id) closeSource();
  }, [closeSource]);
  const refuseSourceRequest = useCallback((request: SourceRequest, citationId: string) => {
    if (request.signal.aborted || pending.current?.id !== request.id) return;
    setRefusedCitationIds((previous) => new Set([...previous, citationId]));
    pending.current.controller.abort();
    pending.current = null;
    selected.current = null;
    setSelectedSource(null);
  }, []);
  const refuseSource = useCallback((source: PreviewDocument) => {
    const current = pending.current;
    if (!current || source.previewRequestId !== current.id || selected.current?.previewRequestId !== current.id) return;
    if ("id" in source && typeof source.id === "string") {
      refuseSourceRequest({ id: current.id, signal: current.controller.signal }, source.id);
    } else {
      closeSource();
    }
  }, [closeSource, refuseSourceRequest]);
  const openSource = useCallback((source: PreviewDocument) => {
    completeSourceRequest(beginSourceRequest(), source);
  }, [beginSourceRequest, completeSourceRequest]);
  useEffect(() => () => { pending.current?.controller.abort(); }, []);
  const value = useMemo(
    () => ({ selectedSource, openSource, closeSource, beginSourceRequest, completeSourceRequest, cancelSourceRequest, refuseSourceRequest, refuseSource, refusedCitationIds }),
    [closeSource, openSource, selectedSource, beginSourceRequest, completeSourceRequest, cancelSourceRequest, refuseSourceRequest, refuseSource, refusedCitationIds],
  );

  return (
    <CitationContext.Provider value={value}>
      {children}
    </CitationContext.Provider>
  );
}

export function useCitations(): CitationContextValue {
  const value = useContext(CitationContext);
  if (!value) {
    throw new Error("useCitations must be used within CitationProvider");
  }
  return value;
}
