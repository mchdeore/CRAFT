import {
  useCitations,
  type CitationSource,
} from "@/components/citations/citation-context";
import {
  citationLinks,
  isCitationURL,
  useCitationStates,
} from "@/components/citations/citation-records";
import { cn } from "@/lib/utils";
import { useAuiState } from "@assistant-ui/react";
import { useMemo, type ComponentProps, type FC } from "react";

export const CitationLink: FC<ComponentProps<"a">> = ({
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
