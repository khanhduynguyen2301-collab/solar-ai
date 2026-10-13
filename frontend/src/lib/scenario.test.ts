import { afterEach, describe, expect, it, vi } from "vitest";

import { loadFrontendDataResult, resolveMockFrontendDataPath } from "./frontend-data.server";
import { describePresetWeather, describeScenario, initialDayFor, initialHourFor, parseScenario, scenarioFailureMessage, serializeScenario, type ScenarioPreset } from "./scenario";
import { SolarApiError, apiSettings, clearSolarApiCache, fetchDayHours, fetchPresets, fetchRecommendation, looksLikeWakingUp } from "./solar-api.server";

const SCENARIO = { timestamp: "2025-06-05T19:00:00Z", angle: 60 };
const PRESET: ScenarioPreset = {
  id: "sunniest", label: "Sunniest hour", timestamp: "2025-06-05T19:00:00Z",
  weather: { temperature_c: 24.2, cloud_cover_pct: 3.4, wind_speed_kmh: 9, wind_gust_kmh: 21.6, ghi_wm2: 911.7, sun_elevation_deg: 61 },
};

function respondWith(status: number, body: unknown) {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  clearSolarApiCache();
});

describe("scenario cookie", () => {
  it("round-trips a valid scenario", () => {
    expect(parseScenario(serializeScenario(SCENARIO))).toEqual(SCENARIO);
  });

  it.each([
    ["nothing", undefined],
    ["text that is not JSON", "not json"],
    ["an angle above the limit", JSON.stringify({ timestamp: SCENARIO.timestamp, angle: 91 })],
    ["a negative angle", JSON.stringify({ timestamp: SCENARIO.timestamp, angle: -1 })],
    ["a timestamp that is not a whole UTC hour", JSON.stringify({ timestamp: "2025-06-05T19:30:00Z", angle: 35 })],
    ["a timestamp carrying a path", JSON.stringify({ timestamp: "../../etc/passwd", angle: 35 })],
    ["an angle given as text", JSON.stringify({ timestamp: SCENARIO.timestamp, angle: "60" })],
  ])("rejects %s", (_name, value) => {
    expect(parseScenario(value)).toBeNull();
  });
});

describe("scenario wording", () => {
  it("describes the chosen hour and starting angle", () => {
    expect(describeScenario(SCENARIO)).toBe("2025-06-05 at 19:00 UTC, row starting at 60°");
  });

  it("summarises why a preset was picked, using its own weather", () => {
    expect(describePresetWeather(PRESET)).toBe("2025-06-05 19:00 UTC · sunlight 912 W/m² · cloud 3% · gusts 22 km/h");
  });

  it("opens on the visitor's day, then the first preset's, then the first day with data", () => {
    const days = ["2025-01-15", "2025-06-05", "2025-09-01"];
    expect(initialDayFor({ timestamp: "2025-09-01T10:00:00Z", angle: 35 }, [PRESET], days)).toBe("2025-09-01");
    expect(initialDayFor(null, [PRESET], days)).toBe("2025-06-05");
    expect(initialDayFor(null, [], days)).toBe("2025-01-15");
    expect(initialDayFor({ timestamp: "2024-01-01T00:00:00Z", angle: 35 }, [], days)).toBe("2025-01-15");
    expect(initialDayFor(null, [], [])).toBeNull();
  });

  it("opens the hour picker on the visitor's hour, then a preset's, then the day's first", () => {
    const hours = ["2025-06-05T00:00:00Z", "2025-06-05T18:00:00Z", "2025-06-05T19:00:00Z"];
    expect(initialHourFor({ timestamp: "2025-06-05T18:00:00Z", angle: 35 }, [PRESET], hours)).toBe("2025-06-05T18:00:00Z");
    expect(initialHourFor(null, [PRESET], hours)).toBe("2025-06-05T19:00:00Z");
    expect(initialHourFor(null, [], hours)).toBe("2025-06-05T00:00:00Z");
    expect(initialHourFor(null, [PRESET], [])).toBe("");
  });

  it("turns API failures into messages a visitor can act on, without internals", () => {
    expect(scenarioFailureMessage({ status: 404, code: "UNKNOWN_HOUR" })).toMatch(/not in the recorded data/);
    expect(scenarioFailureMessage({ status: 429, code: "RATE_LIMITED" })).toMatch(/busy/);
    expect(scenarioFailureMessage({ status: 401, code: "UNAUTHORIZED" })).toMatch(/not set up correctly/);
    expect(scenarioFailureMessage({ status: null, code: "INVALID_PAYLOAD" })).toMatch(/did not pass/);
    expect(scenarioFailureMessage(null)).toMatch(/waking up/);
  });
});

describe("live API client", () => {
  it("is off unless an address is configured", () => {
    expect(apiSettings({})).toBeNull();
    expect(apiSettings({ SOLAR_API_URL: "  " })).toBeNull();
    expect(apiSettings({ SOLAR_API_URL: "https://api.example/", SOLAR_API_KEY: " secret " })).toEqual({ url: "https://api.example", key: "secret" });
    expect(apiSettings({ SOLAR_API_URL: "http://localhost:8000" })).toEqual({ url: "http://localhost:8000", key: null });
  });

  it("asks for a recorded hour with the key and never asks for explanations", async () => {
    vi.stubEnv("SOLAR_API_URL", "https://api.example");
    vi.stubEnv("SOLAR_API_KEY", "server-side-key");
    const fetchMock = respondWith(200, { any: "payload" });

    await expect(fetchRecommendation(SCENARIO)).resolves.toEqual({ any: "payload" });

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://api.example/recommendations");
    expect((init.headers as Record<string, string>)["x-api-key"]).toBe("server-side-key");
    expect(JSON.parse(init.body as string)).toEqual({ mode: "recorded", timestamp: SCENARIO.timestamp, current_angle_deg: 60, explain: false });
  });

  it("asks the API once for the same scenario, and again for a different one", async () => {
    vi.stubEnv("SOLAR_API_URL", "https://api.example");
    const fetchMock = respondWith(200, {});

    await fetchRecommendation(SCENARIO);
    await fetchRecommendation(SCENARIO);
    await fetchRecommendation({ ...SCENARIO, angle: 30 });

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("reports the API's own error code and does not keep the failure", async () => {
    vi.stubEnv("SOLAR_API_URL", "https://api.example");
    const fetchMock = respondWith(404, { error: { code: "UNKNOWN_HOUR", message: "No recorded hour" } });

    await expect(fetchRecommendation(SCENARIO)).rejects.toMatchObject({ status: 404, code: "UNKNOWN_HOUR" });
    await expect(fetchRecommendation(SCENARIO)).rejects.toBeInstanceOf(SolarApiError);

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("reports an unreachable API without a status, after trying again", async () => {
    vi.stubEnv("SOLAR_API_URL", "https://api.example");
    vi.useFakeTimers();
    const fetchMock = vi.fn(async () => { throw new TypeError("fetch failed"); });
    vi.stubGlobal("fetch", fetchMock);

    const outcome = expect(fetchRecommendation(SCENARIO)).rejects.toMatchObject({ status: null, code: null });
    await vi.runAllTimersAsync();
    await outcome;

    expect(fetchMock).toHaveBeenCalledTimes(3);
    vi.useRealTimers();
  });

  it("tries again only for failures that look like a service still starting", () => {
    expect(looksLikeWakingUp(new SolarApiError("unreachable", null, null))).toBe(true);
    expect(looksLikeWakingUp(new SolarApiError("gateway", 503, null))).toBe(true);
    expect(looksLikeWakingUp(new SolarApiError("no instance", 429, null))).toBe(true);
    expect(looksLikeWakingUp(new SolarApiError("our own limit", 429, "RATE_LIMITED"))).toBe(false);
    expect(looksLikeWakingUp(new SolarApiError("bad key", 401, "UNAUTHORIZED"))).toBe(false);
    expect(looksLikeWakingUp(new SolarApiError("unknown hour", 404, "UNKNOWN_HOUR"))).toBe(false);
    expect(looksLikeWakingUp(new SolarApiError("not configured", null, "NOT_CONFIGURED"))).toBe(false);
    expect(looksLikeWakingUp(new Error("something else"))).toBe(false);
  });

  it("gets an answer from an API that refuses the first request while it starts", async () => {
    vi.stubEnv("SOLAR_API_URL", "https://api.example");
    vi.useFakeTimers();
    const replies = [new Response("starting", { status: 503 }), new Response(JSON.stringify({ timestamps: ["2025-06-05T19:00:00Z"] }), { status: 200 })];
    const fetchMock = vi.fn(async () => replies.shift() as Response);
    vi.stubGlobal("fetch", fetchMock);

    const hours = fetchDayHours("2025-06-05");
    await vi.runAllTimersAsync();

    await expect(hours).resolves.toEqual(["2025-06-05T19:00:00Z"]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    vi.useRealTimers();
  });

  it("refuses to call anything when no API is configured", async () => {
    const fetchMock = respondWith(200, {});

    await expect(fetchPresets()).rejects.toMatchObject({ code: "NOT_CONFIGURED" });

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects a listing in an unexpected shape", async () => {
    vi.stubEnv("SOLAR_API_URL", "https://api.example");
    respondWith(200, { scenarios: [{ id: "sunniest" }] });

    await expect(fetchPresets()).rejects.toMatchObject({ code: "INVALID_PAYLOAD" });
  });

  it("lists one day's hours", async () => {
    vi.stubEnv("SOLAR_API_URL", "https://api.example");
    const fetchMock = respondWith(200, { day: "2025-06-05", timestamps: ["2025-06-05T18:00:00Z", "2025-06-05T19:00:00Z"] });

    await expect(fetchDayHours("2025-06-05")).resolves.toEqual(["2025-06-05T18:00:00Z", "2025-06-05T19:00:00Z"]);

    expect((fetchMock.mock.calls[0] as unknown as [string])[0]).toBe("https://api.example/hours/2025-06-05");
  });
});

describe("page data loader", () => {
  it("shows the recorded file, with no scenario and no notice, outside a visitor's request", async () => {
    vi.stubEnv("SOLAR_API_URL", "https://api.example");
    const fetchMock = respondWith(200, {});

    const result = await loadFrontendDataResult();

    expect(result).toMatchObject({ ok: true, scenario: null, notice: null });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("still loads an explicitly named file", async () => {
    const result = await loadFrontendDataResult(resolveMockFrontendDataPath());

    expect(result).toMatchObject({ ok: true, sourcePath: resolveMockFrontendDataPath(), scenario: null, notice: null });
  });
});
