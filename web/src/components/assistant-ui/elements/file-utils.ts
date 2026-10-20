import type { FC } from "react";
import { cva } from "class-variance-authority";
import {
  FileIcon,
  FileTextIcon,
  ImageIcon,
  MusicIcon,
  VideoIcon,
  BracesIcon,
} from "lucide-react";

export const fileVariants = cva(
  "aui-file-root inline-flex items-center gap-3 rounded-lg transition-colors",
  {
    variants: {
      variant: {
        outline: "border-border hover:bg-muted/50 border",
        ghost: "hover:bg-muted/50",
        muted: "bg-muted/50 hover:bg-muted/70",
      },
      size: {
        sm: "px-2.5 py-1.5 text-xs",
        default: "px-3 py-2 text-sm",
        lg: "px-4 py-3 text-base",
      },
    },
    defaultVariants: {
      variant: "outline",
      size: "default",
    },
  },
);

export const fileIcons = {
  file: FileIcon,
  image: ImageIcon,
  pdf: FileTextIcon,
  json: BracesIcon,
  text: FileTextIcon,
  audio: MusicIcon,
  video: VideoIcon,
};

export function getMimeTypeIconKey(mimeType: string): keyof typeof fileIcons {
  const type = mimeType.toLowerCase();
  if (type.startsWith("image/")) {
    return "image";
  }
  if (type === "application/pdf") {
    return "pdf";
  }
  if (type === "application/json") {
    return "json";
  }
  if (type.startsWith("text/")) {
    return "text";
  }
  if (type.startsWith("audio/")) {
    return "audio";
  }
  if (type.startsWith("video/")) {
    return "video";
  }
  return "file";
}

export function getMimeTypeIcon(mimeType: string): FC<{ className?: string }> {
  return fileIcons[getMimeTypeIconKey(mimeType)];
}

export type FileDataKind = "data-uri" | "url" | "base64" | "id";

export function getFileDataKind(
  data: string,
  sourceType?: "url" | "id",
): FileDataKind {
  if (sourceType === "url" && /^data:/i.test(data)) return "data-uri";
  if (sourceType) return sourceType;
  if (/^data:/i.test(data)) return "data-uri";
  if (/^https?:\/\//i.test(data)) return "url";
  return "base64";
}

function getBase64PayloadSize(payload: string): number {
  const padding = payload.endsWith("==") ? 2 : payload.endsWith("=") ? 1 : 0;
  const firstNonBase64 = payload.search(/[^A-Za-z\d+/]/);
  if (
    (firstNonBase64 !== -1 && firstNonBase64 !== payload.length - padding) ||
    payload.length % 4 === 1 ||
    (padding > 0 && payload.length % 4 !== 0)
  ) {
    return 0;
  }
  return Math.floor((payload.length * 3) / 4) - padding;
}

export function getBase64Size(base64: string): number {
  const payload = /[\t\n\f\r ]/.test(base64)
    ? base64.replace(/[\t\n\f\r ]/g, "")
    : base64;
  return getBase64PayloadSize(payload);
}

export function getDataUrlSize(data: string): number {
  const fragment = data.indexOf("#");
  const end = fragment < 0 ? data.length : fragment;
  const comma = data.indexOf(",");
  if (comma < 0 || comma >= end) {
    return 0;
  }
  let payload = data.slice(comma + 1, end);
  if (/;base64$/i.test(data.slice(0, comma))) {
    if (/[%\t\n\f\r ]/.test(payload)) {
      payload = payload
        .replace(/%([\da-f]{2})/gi, (_match, hex: string) =>
          String.fromCharCode(Number.parseInt(hex, 16)),
        )
        .replace(/[\t\n\f\r ]/g, "");
    }
    return getBase64PayloadSize(payload);
  }

  // Each percent escape is one byte, including octets that are not valid UTF-8.
  return new TextEncoder().encode(payload.replace(/%[\da-f]{2}/gi, "_"))
    .byteLength;
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
