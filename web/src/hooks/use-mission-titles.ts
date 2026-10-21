import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api-client";

export function useMissionTitles(keys: readonly (string | null | undefined)[]) {
  const key = [...new Set(keys.filter(Boolean))].sort().join("\n");
  const [result, setResult] = useState<{ key: string; titles: ReadonlyMap<string, string> }>();
  useEffect(() => {
    if (!key) return;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 9500);
    void apiFetch("/api/cube/missions?limit=100", { signal: controller.signal, cache: "no-store" }).then(async response => {
      if (!response.ok) return;
      const rows: unknown = await response.json();
      if (!Array.isArray(rows) || rows.length > 100) return;
      const titles = new Map<string, string>();
      for (const row of rows) {
        if (row && typeof row.key === "string" && typeof row.title === "string" && row.title.length <= 512 && ![...row.title].some(character => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127)) titles.set(row.key, row.title);
      }
      if (!controller.signal.aborted) setResult({ key, titles });
    }).catch(() => {}).finally(() => clearTimeout(timer));
    return () => { clearTimeout(timer); controller.abort(); };
  }, [key]);
  return result?.key === key ? result.titles : undefined;
}
