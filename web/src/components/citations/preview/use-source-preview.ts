import { apiFetch } from "@/lib/api-client";
import type { PreviewDocument } from "@/components/citations/citation-context";
import { useCitations } from "@/components/citations/citation-context";
import type { DocumentFormat } from "@/components/citations/preview/renderer-contract";
import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
} from "react";

export type DocumentPreview =
  | { type: "loading" }
  | { type: "ready"; format: DocumentFormat; blob: Blob }
  | { type: "unsupported"; mediaType: string }
  | { type: "error"; message: string };

const DOCX_MEDIA_TYPE =
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
const XLSX_MEDIA_TYPE =
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

export function useDocumentPreview(source: PreviewDocument): DocumentPreview {
  const { refuseSource } = useCitations();
  const key = sourcePreviewKey(source);
  const [state, setState] = useState<{ key: string; preview: DocumentPreview } | null>(null);

  useEffect(() => {
    const controller = new AbortController();

    void loadDocumentPreview(source, controller.signal).then((preview) => {
      if (!controller.signal.aborted) {
        if (preview.type === "error") refuseSource(source);
        setState({ key, preview });
      }
    });

    return () => {
      controller.abort();
    };
  }, [key, source, refuseSource]);

  // Identity is checked during render, before effects can expose old bytes.
  return state?.key === key ? state.preview : { type: "loading" };
}

const MAX_PREVIEW_BYTES = 10 * 1024 * 1024;
const PREVIEW_ERROR = "This source cannot be previewed.";

export async function loadDocumentPreview(source: PreviewDocument, signal: AbortSignal, fetcher?: typeof fetch): Promise<DocumentPreview> {
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
  try {
    const protectedURL = /^\/api\/library\/documents\/[A-Za-z0-9_-]{1,128}\/preview(?:\?[^#]*)?$/.test(source.previewURL);
    const workspaceURL = /^\/api\/conversations\/[A-Za-z0-9_-]+\/files\/(?:[A-Za-z0-9_.-]|%[a-fA-F0-9]{2})+\/content$/.test(source.previewURL);
    if ((!protectedURL && !workspaceURL) || signal.aborted) throw new Error();
    const request: typeof fetch = fetcher ?? apiFetch;
    const response = await request(source.previewURL, { credentials: "same-origin", cache: "no-store", signal });
    const mediaType = (response.headers.get("Content-Type") ?? "").split(";", 1)[0]!.trim().toLowerCase();
    const format = documentFormat(mediaType);
    const contentLength = response.headers.get("Content-Length");
    if (!response.ok || signal.aborted || (contentLength !== null && (!/^\d+$/.test(contentLength) || Number(contentLength) > MAX_PREVIEW_BYTES))) {
      await response.body?.cancel();
      throw new Error();
    }
    if (!response.body) throw new Error();
    if (!format) {
      await response.body.cancel();
      if (signal.aborted || !protectedURL) throw new Error();
      return { type: "unsupported", mediaType };
    }
    reader = response.body.getReader();
    const onAbort = () => { void reader?.cancel().catch(() => {}); };
    signal.addEventListener("abort", onAbort, { once: true });
    const chunks: Uint8Array<ArrayBuffer>[] = [];
    let size = 0;
    try {
      while (true) {
        if (signal.aborted) throw new Error();
        const chunk = await reader.read();
        if (signal.aborted) throw new Error();
        if (chunk.done) break;
        size += chunk.value.byteLength;
        if (size > MAX_PREVIEW_BYTES) { await reader.cancel(); throw new Error(); }
        chunks.push(new Uint8Array(chunk.value));
      }
      if (signal.aborted) throw new Error();
      return { type: "ready", format, blob: new Blob(chunks, { type: response.headers.get("Content-Type") ?? "" }) };
    } finally {
      signal.removeEventListener("abort", onAbort);
      reader.releaseLock();
    }
  } catch {
    return { type: "error", message: PREVIEW_ERROR };
  }
}

export function useResizableSourcePanel() {
  const [width, setWidth] = useState(() => defaultPanelWidth());
  const drag = useRef<{ pointerX: number; width: number } | undefined>(
    undefined,
  );
  const clampWidth = (nextWidth: number) =>
    Math.min(Math.max(nextWidth, 360), window.innerWidth * 0.8);

  return {
    width,
    resizeHandleProps: {
      onPointerDown: (event: PointerEvent<HTMLDivElement>) => {
        drag.current = { pointerX: event.clientX, width };
        event.currentTarget.setPointerCapture(event.pointerId);
      },
      onPointerMove: (event: PointerEvent<HTMLDivElement>) => {
        if (!drag.current) return;
        setWidth(
          clampWidth(
            drag.current.width + drag.current.pointerX - event.clientX,
          ),
        );
      },
      onPointerUp: (event: PointerEvent<HTMLDivElement>) => {
        drag.current = undefined;
        event.currentTarget.releasePointerCapture(event.pointerId);
      },
      onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => {
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        event.preventDefault();
        setWidth((current) =>
          clampWidth(current + (event.key === "ArrowLeft" ? 24 : -24)),
        );
      },
    },
  };
}

export function useIsDesktopPreview(): boolean {
  const [isDesktop, setIsDesktop] = useState(() =>
    typeof window === "undefined"
      ? false
      : window.matchMedia("(min-width: 768px)").matches,
  );

  useEffect(() => {
    const query = window.matchMedia("(min-width: 768px)");
    const update = () => setIsDesktop(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);

  return isDesktop;
}

export function sourcePreviewKey(source: PreviewDocument): string {
  return JSON.stringify([source.previewURL, source.locator ?? "", source.previewRequestId ?? 0]);
}

function defaultPanelWidth(): number {
  if (typeof window === "undefined") return 560;
  return Math.min(720, Math.max(360, window.innerWidth * 0.5));
}

function documentFormat(mediaType: string): DocumentFormat | undefined {
  if (mediaType === DOCX_MEDIA_TYPE) return "docx";
  if (mediaType === XLSX_MEDIA_TYPE) return "xlsx";
  if (mediaType === "application/pdf") return "pdf";
  if (mediaType === "image/png") return "png";
  if (mediaType === "text/plain" || mediaType === "text/markdown" || mediaType === "text/csv") return "text";
  return undefined;
}
