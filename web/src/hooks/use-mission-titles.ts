import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api-client";

const record = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);

export function useMissionTitles(keys: readonly (string | null | undefined)[]) {
  const key = [...new Set(keys.filter(Boolean))].sort().join("\n");
  const [result, setResult] = useState<{ key: string; titles: ReadonlyMap<string, string> }>();
  useEffect(() => {
    if (!key) return;
    const controller = new AbortController();
    const requested = key.split("\n"), titles = new Map<string, string>();
    const publish = () => setResult({ key, titles: new Map(titles) });
    const timer = setTimeout(() => { controller.abort(); publish(); }, 9500);
    let next = 0;
    const load = async () => {
      while (!controller.signal.aborted && next < requested.length) {
        const missionKey = requested[next++];
        try {
          const response = await apiFetch(`/api/cube/missions/${encodeURIComponent(missionKey)}`, { signal: controller.signal, cache: "no-store" });
          if (!response.ok) continue;
          const body: unknown = await response.json();
          const mission = record(body) && record(body.mission) ? body.mission : undefined;
          if (mission && mission.key === missionKey && typeof mission.title === "string" && mission.title.trim() && mission.title.length <= 512 && ![...mission.title].some(character => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127) && !controller.signal.aborted) titles.set(missionKey, mission.title);
        } catch { /* Unavailable mission titles are shown explicitly in document labels. */ }
      }
    };
    void Promise.all(Array.from({ length: Math.min(3, requested.length) }, load)).then(() => {
      if (!controller.signal.aborted) publish();
    }).finally(() => clearTimeout(timer));
    return () => { clearTimeout(timer); controller.abort(); };
  }, [key]);
  return result?.key === key ? result.titles : undefined;
}
