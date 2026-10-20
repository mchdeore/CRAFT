import { useEffect, useState } from "react";
import type { DocumentRendererProps } from "@/components/citations/preview/renderer-contract";

export function TextContent({ text }: { text: string }) {
  return (
    <div className="p-4">
      <pre className="whitespace-pre-wrap break-words font-sans text-sm leading-relaxed">{text.slice(0, 100000)}</pre>
      {text.length > 100000 && <p className="text-muted-foreground mt-3 text-xs">Preview truncated. Download the source to read the remaining text.</p>}
    </div>
  );
}

export function TextRenderer({ blob }: DocumentRendererProps) {
  const [state, setState] = useState<{ blob: Blob; text: string } | null>(null);
  useEffect(() => {
    let current = true;
    void blob.text().then((text) => { if (current) setState({ blob, text }); }).catch(() => { if (current) setState({ blob, text: "Preview unavailable." }); });
    return () => { current = false; };
  }, [blob]);
  return state?.blob === blob ? <TextContent text={state.text} /> : <p className="text-muted-foreground p-4 text-sm">Loading preview…</p>;
}
