import type { Tags } from "craft-api/api/types";

const words = (value: string) => value.replace(/^st\d+-/, "").replaceAll(/[-_]/g, " ");
const sentence = (value: string) => value.charAt(0).toUpperCase() + value.slice(1);

export function documentLabel(title: string, tags?: Tags, missions?: ReadonlyMap<string, string>) {
  const name = title.trim() || "Untitled document";
  const mission = tags?.mission && (missions?.get(tags.mission) ?? `Mission title ${missions ? "unavailable" : "loading"} (${tags.mission})`);
  return mission ? `${name} — ${mission}` : name;
}

export function documentScope(version: string | number, classification?: string, simulated?: boolean, tags?: Tags) {
  return [tags?.silo && sentence(words(tags.silo)), tags?.stage && sentence(words(tags.stage)), `Version ${version}`, classification && sentence(words(classification)), simulated && "Simulated"].filter(Boolean).join(" · ");
}
