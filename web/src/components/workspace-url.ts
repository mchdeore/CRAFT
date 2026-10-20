export type WorkspaceConfiguration =
  | { status: "configured"; url: string }
  | { status: "missing" | "invalid" };

export function resolveUserWorkspaceURL(value: string | undefined, ownerID: string, craftOrigin: string): WorkspaceConfiguration {
  if (!ownerID || !value?.trim()) return { status: "missing" };
  try {
    const entries: unknown = JSON.parse(value);
    if (typeof entries !== "object" || entries === null || Array.isArray(entries)) return { status: "invalid" };
    if (!Object.hasOwn(entries, ownerID)) return { status: "missing" };
    const entry = (entries as Record<string, unknown>)[ownerID];
    if (typeof entry !== "string") return { status: "invalid" };
    return resolveWorkspaceURL(entry, craftOrigin);
  } catch {
    return { status: "invalid" };
  }
}

export function resolveWorkspaceURL(value: string | undefined, craftOrigin: string): WorkspaceConfiguration {
  if (!value?.trim()) return { status: "missing" };
  try {
    const url = new URL(value.trim());
    if (url.protocol !== "http:" || !["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)
      || url.username || url.password || url.search || url.hash || url.pathname !== "/"
      || url.origin === new URL(craftOrigin).origin) return { status: "invalid" };
    url.pathname = "/lab";
    return { status: "configured", url: url.href };
  } catch {
    return { status: "invalid" };
  }
}
