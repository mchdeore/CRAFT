import { Button } from "@/components/ui/button";
import { Maximize2Icon, MinusIcon, PlusIcon } from "lucide-react";
import type { ReactNode } from "react";

const MIN_PREVIEW_ZOOM = 0.3;
const MAX_PREVIEW_ZOOM = 2;
const ZOOM_STEP = 0.1;

// Shared by the Office-format renderers so their zoom controls behave alike.
export function OfficePreviewToolbar({
  application,
  color,
  icon,
  zoom,
  onZoomChange,
  onFitWidth,
  children,
}: {
  application: string;
  color: string;
  icon: ReactNode;
  zoom: number;
  onZoomChange: (zoom: number) => void;
  onFitWidth: () => void;
  children?: ReactNode;
}) {
  return (
    <div className="bg-background z-40 flex h-11 shrink-0 items-center gap-2 border-b px-2 shadow-sm">
      <div className="mr-auto flex items-center gap-2 px-1 text-sm font-medium">
        <span
          className="flex size-7 items-center justify-center rounded text-white"
          style={{ backgroundColor: color }}
        >
          {icon}
        </span>
        <span>{application} preview</span>
      </div>

      {children}
      {children && <div className="bg-border mx-1 h-5 w-px" />}

      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        disabled={zoom <= MIN_PREVIEW_ZOOM}
        onClick={() => onZoomChange(clampPreviewZoom(zoom - ZOOM_STEP))}
        aria-label={`Zoom ${application.toLowerCase()} out`}
      >
        <MinusIcon />
      </Button>
      <span className="w-10 text-center text-xs tabular-nums">
        {Math.round(zoom * 100)}%
      </span>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        disabled={zoom >= MAX_PREVIEW_ZOOM}
        onClick={() => onZoomChange(clampPreviewZoom(zoom + ZOOM_STEP))}
        aria-label={`Zoom ${application.toLowerCase()} in`}
      >
        <PlusIcon />
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        onClick={onFitWidth}
        aria-label={`Fit ${application.toLowerCase()} width`}
        title={`Fit ${application.toLowerCase()} width`}
      >
        <Maximize2Icon />
      </Button>
    </div>
  );
}

function clampPreviewZoom(zoom: number): number {
  return Math.min(
    MAX_PREVIEW_ZOOM,
    Math.max(MIN_PREVIEW_ZOOM, Math.round(zoom * 10) / 10),
  );
}
