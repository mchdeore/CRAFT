import {
  useCitations,
  type CitationSource,
} from "@/components/citations/citation-context";
import {
  citationLinks,
  isCitationURL,
  sourceLink,
  useCitationStates,
  type SourceLink,
} from "@/components/citations/citation-records";
import { FileTextIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { useAuiState } from "@assistant-ui/react";
import { useMemo, type ComponentProps, type FC } from "react";

export const CitationLink: FC<ComponentProps<"a">> = ({
  className,
  href,
  onClick,
  ...props
}) => {
  const source = sourceLink(href);
  if (source) return <SourceChip source={source} className={className} onClick={onClick} {...props} />;
  return <AnswerLink className={className} href={href} onClick={onClick} {...props} />;
};

// A cited document as a compact chip; opening it shows the original beside the chat.
const SourceChip: FC<ComponentProps<"a"> & { source: SourceLink }> = ({ source, className, onClick, children, ...props }) => {
  const { openSource } = useCitations();
  const base = `/api/library/documents/${encodeURIComponent(source.item)}`;
  const label = typeof children === "string" || typeof children === "number" ? String(children) : "Source";
  const where = source.block ? `passage ${source.block}` : `version ${source.version}`;
  return (
    <a
      {...props}
      href={`${base}/preview?version=${source.version}`}
      title={`${label} · ${where}`}
      className={cn("bg-primary/8 text-primary hover:bg-primary/15 mx-0.5 inline-flex max-w-64 items-center gap-1 rounded-md px-1.5 py-px align-baseline text-xs font-medium no-underline", className)}
      onClick={(event) => {
        onClick?.(event);
        if (event.defaultPrevented) return;
        event.preventDefault();
        openSource({
          title: label, mediaType: "", sourceID: source.item, version: String(source.version),
          previewURL: `${base}/preview?version=${source.version}`, downloadURL: `${base}/download?version=${source.version}`,
          locationLabel: where, subtitle: `Version ${source.version} · ${where}`,
        });
      }}
    >
      <FileTextIcon className="size-3 shrink-0" />
      <span className="truncate">{children}</span>
    </a>
  );
};

const AnswerLink: FC<ComponentProps<"a">> = ({
  className,
  href,
  onClick,
  ...props
}) => {
  const { openSource } = useCitations();
  const messageParts = useAuiState((state) => state.message.parts);
  const isCitation = href ? isCitationURL(href) : false;
  const urls = useMemo(() => (isCitation && href ? [href] : []), [href, isCitation]);
  const states = useCitationStates(urls);
  const state = href ? states.get(href) : undefined;
  const number = useMemo(
    () => citationLinks(messageParts).find((link) => link.url === href)?.number,
    [href, messageParts],
  );
  const unverified = state?.status === "unverified";

  return (
    <a
      href={href}
      className={cn(
        isCitation
          ? "bg-muted hover:bg-accent inline-flex rounded px-1 text-xs font-medium no-underline"
          : "text-primary hover:text-primary/80 underline underline-offset-2",
        unverified && "text-muted-foreground line-through",
        className,
      )}
      title={
        unverified
          ? "Unverified citation: the agent never retrieved this passage"
          : state?.status === "error"
            ? "This citation could not be loaded"
            : undefined
      }
      onClick={(event) => {
        onClick?.(event);
        if (!event.defaultPrevented && isCitation) {
          // Citation URLs are API endpoints. Always keep them inside the app,
          // even if the citation is unverified and no source card exists.
          event.preventDefault();
          if (state?.status === "verified" && number !== undefined) {
            const source: CitationSource = { ...state.record, number };
            openSource(source);
          }
        }
      }}
      {...props}
    />
  );
};
