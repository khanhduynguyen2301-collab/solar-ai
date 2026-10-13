import { z } from "zod";

import { DAY_PATTERN, presetSchema, type Scenario, type ScenarioPreset } from "./scenario";

/**
 * Server-side client for the hosted inference API. The key stays on the server:
 * this module must never be imported by a client component.
 */
export const API_URL_VARIABLE = "SOLAR_API_URL";
export const API_KEY_VARIABLE = "SOLAR_API_KEY";

// Long enough for the hosted API to wake from idle; a visitor is told it may take a few seconds.
const REQUEST_TIMEOUT_MS = 25_000;
// The same hour and angle always give the same decision, so a short cache spares the API on every page view.
const RECOMMENDATION_TTL_MS = 5 * 60 * 1000;
const LISTING_TTL_MS = 10 * 60 * 1000;
const MAX_CACHE_ENTRIES = 300;
// A hosted API that has gone idle refuses or drops the first requests while it starts, so those are tried again.
const WAKE_UP_RETRIES = 2;
const WAKE_UP_RETRY_DELAY_MS = 3_000;

export class SolarApiError extends Error {
  constructor(message: string, readonly status: number | null, readonly code: string | null) {
    super(message);
    this.name = "SolarApiError";
  }
}

export type ApiSettings = { url: string; key: string | null };

/** Where the live API is, or null when this site has not been connected to one. */
export function apiSettings(environment: Record<string, string | undefined> = process.env): ApiSettings | null {
  const url = environment[API_URL_VARIABLE]?.trim().replace(/\/+$/, "");
  if (!url) return null;
  return { url, key: environment[API_KEY_VARIABLE]?.trim() || null };
}

const errorEnvelopeSchema = z.object({ error: z.object({ code: z.string(), message: z.string() }) });

type RequestInitLite = { method: "GET" | "POST"; body?: unknown };

/** True for failures that look like a service still starting: unreachable, a gateway error, or a refusal that is not the API's own. */
export function looksLikeWakingUp(error: unknown): boolean {
  if (!(error instanceof SolarApiError) || error.code !== null) return false;
  return error.status === null || error.status === 429 || error.status >= 500;
}

async function request(path: string, init: RequestInitLite = { method: "GET" }, retryDelayMs = WAKE_UP_RETRY_DELAY_MS): Promise<unknown> {
  for (let attempt = 0; ; attempt++) {
    try {
      return await requestOnce(path, init);
    } catch (error) {
      if (attempt >= WAKE_UP_RETRIES || !looksLikeWakingUp(error)) throw error;
      await new Promise(resolve => setTimeout(resolve, retryDelayMs));
    }
  }
}

async function requestOnce(path: string, init: RequestInitLite): Promise<unknown> {
  const settings = apiSettings();
  if (!settings) throw new SolarApiError("The live API is not configured", null, "NOT_CONFIGURED");
  let response: Response;
  try {
    response = await fetch(settings.url + path, {
      method: init.method,
      headers: { accept: "application/json", ...(init.body === undefined ? {} : { "content-type": "application/json" }), ...(settings.key ? { "x-api-key": settings.key } : {}) },
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
      cache: "no-store",
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } catch {
    throw new SolarApiError("The live API could not be reached", null, null);
  }
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new SolarApiError("The live API returned a reply that is not JSON", response.status, null);
  }
  if (!response.ok) {
    const envelope = errorEnvelopeSchema.safeParse(body);
    throw new SolarApiError(envelope.success ? envelope.data.error.message : `HTTP ${response.status}`, response.status, envelope.success ? envelope.data.error.code : null);
  }
  return body;
}

type CacheEntry = { expires: number; value: Promise<unknown> };
const cache = new Map<string, CacheEntry>();

/** One in-flight or recent result per key. A failure is not kept, so the next call tries again. */
function cached<T>(key: string, ttlMs: number, load: () => Promise<T>): Promise<T> {
  const now = Date.now();
  const hit = cache.get(key);
  if (hit && hit.expires > now) return hit.value as Promise<T>;
  const value = load();
  cache.set(key, { expires: now + ttlMs, value });
  value.catch(() => { if (cache.get(key)?.value === value) cache.delete(key); });
  if (cache.size > MAX_CACHE_ENTRIES) cache.delete(cache.keys().next().value as string);
  return value;
}

export function clearSolarApiCache(): void {
  cache.clear();
}

/** The API's decision payload for one scenario, not yet validated against the dashboard's schema. */
export function fetchRecommendation(scenario: Scenario): Promise<unknown> {
  return cached(`recommendation:${scenario.timestamp}:${scenario.angle}`, RECOMMENDATION_TTL_MS, () =>
    // Explanations are never requested for a visitor, so a public button cannot spend LLM credit.
    request("/recommendations", { method: "POST", body: { mode: "recorded", timestamp: scenario.timestamp, current_angle_deg: scenario.angle, explain: false } }));
}

const scenariosSchema = z.object({ scenarios: z.array(presetSchema) });
const hoursSchema = z.object({ days: z.array(z.string().regex(DAY_PATTERN)) });
const dayHoursSchema = z.object({ timestamps: z.array(z.string()) });

function parseListing<T>(schema: z.ZodType<T>, body: unknown): T {
  const parsed = schema.safeParse(body);
  if (!parsed.success) throw new SolarApiError("The live API returned a listing in an unexpected shape", null, "INVALID_PAYLOAD");
  return parsed.data;
}

export function fetchPresets(): Promise<ScenarioPreset[]> {
  return cached("presets", LISTING_TTL_MS, async () => parseListing(scenariosSchema, await request("/scenarios")).scenarios);
}

export function fetchDays(): Promise<string[]> {
  return cached("days", LISTING_TTL_MS, async () => parseListing(hoursSchema, await request("/hours")).days);
}

export function fetchDayHours(day: string): Promise<string[]> {
  return cached(`day:${day}`, LISTING_TTL_MS, async () => parseListing(dayHoursSchema, await request(`/hours/${day}`)).timestamps);
}
