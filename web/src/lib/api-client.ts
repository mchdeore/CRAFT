type FetchInput = Parameters<typeof fetch>[0];

export function createAPIClient(
  configuredOrigin?: string,
  fetcher: typeof fetch = (input, init) => globalThis.fetch(input, init),
) {
  const invalidText = (value: string) => value.includes("\\") || value.includes("#") ||
    [...value].some(character => character.charCodeAt(0) <= 32 || character.charCodeAt(0) === 127);
  let origin: string | undefined;
  if (configuredOrigin?.trim()) {
    try {
      const configuredValue = configuredOrigin.trim();
      if (invalidText(configuredValue) || !/^https:\/\/[^/?#]+\/?$/i.test(configuredValue)) throw new Error();
      const value = new URL(configuredValue);
      if (value.protocol !== "https:" || value.username || value.password ||
        value.pathname !== "/" || value.search || value.hash || value.hostname.includes("*")) throw new Error();
      origin = value.origin;
    } catch { throw new TypeError("VITE_API_ORIGIN must configure one HTTPS origin."); }
  }
  const currentOrigin = () => origin ?? globalThis.location?.origin;
  function path(value: string): string {
    if (!value.startsWith("/api/") || invalidText(value)) {
      throw new TypeError("CRAFT API request must use a canonical API path.");
    }
    const parsed = new URL(value, origin ?? "https://craft-api.invalid");
    if (parsed.pathname !== value.split("?", 1)[0]) throw new TypeError("CRAFT API request must use a canonical API path.");
    return value;
  }
  const url = (value: string) => (origin ?? "") + path(value);
  function destination(input: FetchInput): FetchInput {
    if (typeof input === "string" && input.startsWith("/")) return url(input);
    const serialized = input instanceof Request ? input.url : String(input);
    if (invalidText(serialized)) throw new TypeError("CRAFT API request must target the configured API.");
    let parsed: URL;
    try { parsed = new URL(serialized); }
    catch { throw new TypeError("CRAFT API request must target the configured API."); }
    if (!currentOrigin() || parsed.origin !== currentOrigin() || parsed.username || parsed.password || parsed.hash) {
      throw new TypeError("CRAFT API request must target the configured API.");
    }
    const rawPath = serialized.match(/^https?:\/\/[^/?#]+(\/[^#]*)$/i)?.[1];
    if (!rawPath || parsed.pathname !== rawPath.split("?", 1)[0]) {
      throw new TypeError("CRAFT API request must use a canonical API path.");
    }
    path(rawPath);
    return input;
  }
  const request: typeof fetch = async (input, init) =>
    fetcher(destination(input), { ...init, credentials: origin ? "include" : "same-origin" });
  const resourceURL = (value: string) => value.startsWith("/api/") ? url(value) : value;
  const resourceFetch = async (value: string, init?: RequestInit): Promise<Response> => {
    let recognized = value.startsWith("/api/");
    if (!recognized) {
      try { const parsed = new URL(value); recognized = parsed.origin === currentOrigin() && parsed.pathname.startsWith("/api/"); }
      catch { /* Opaque media is outside the API connection. */ }
    }
    return recognized ? request(value, init) : fetcher(value, { ...init, credentials: "omit" });
  };
  return { url, fetch: request, resourceURL, resourceFetch };
}

const client = createAPIClient(import.meta.env?.VITE_API_ORIGIN);
export const apiURL = client.url;
export const apiFetch = client.fetch;
export const apiResourceURL = client.resourceURL;
export const apiResourceFetch = client.resourceFetch;
