import { useEffect, useRef, useState } from "react";
import type { DocumentRendererProps } from "@/components/citations/preview/renderer-contract";

export function PngRenderer({ blob, source }: DocumentRendererProps) {
  const [owner, setOwner] = useState({ blob, generation: 0 });
  // Reset the mounted decoder during render, before replacement effects run.
  if (owner.blob !== blob) {
    setOwner({ blob, generation: owner.generation + 1 });
  }
  return <PngImage key={owner.generation} blob={blob} source={source} />;
}

function PngImage({ blob, source }: DocumentRendererProps) {
  const imageRef = useRef<HTMLImageElement>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const objectURL = URL.createObjectURL(blob);
    const image = imageRef.current;
    image?.setAttribute("src", objectURL);
    return () => {
      image?.removeAttribute("src");
      URL.revokeObjectURL(objectURL);
    };
  }, [blob]);

  if (failed) {
    return <p role="status" className="text-muted-foreground text-sm">PNG preview is unavailable.</p>;
  }
  return (
    <img
      ref={imageRef}
      alt={`PNG source preview: ${source.title}`}
      className="mx-auto block h-auto max-w-full"
      onError={() => setFailed(true)}
    />
  );
}
