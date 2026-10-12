import { z } from "zod";

/** The visitor's chosen scenario: one recorded hour, with the controlled row starting at one angle. */
export const SCENARIO_COOKIE = "solar_scenario";
export const SCENARIO_COOKIE_MAX_AGE_SECONDS = 24 * 60 * 60;
export const MIN_ANGLE_DEG = 0;
export const MAX_ANGLE_DEG = 90;
export const DEFAULT_ANGLE_DEG = 35;

const TIMESTAMP_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:00:00Z$/;
export const DAY_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

export const scenarioSchema = z.object({
  timestamp: z.string().regex(TIMESTAMP_PATTERN),
  angle: z.number().min(MIN_ANGLE_DEG).max(MAX_ANGLE_DEG),
});
export type Scenario = z.infer<typeof scenarioSchema>;

const weatherSchema = z.object({
  temperature_c: z.number(),
  cloud_cover_pct: z.number(),
  wind_speed_kmh: z.number(),
  wind_gust_kmh: z.number(),
  ghi_wm2: z.number(),
  sun_elevation_deg: z.number(),
});

export const presetSchema = z.object({
  id: z.string().min(1),
  label: z.string().min(1),
  timestamp: z.string().regex(TIMESTAMP_PATTERN),
  weather: weatherSchema,
});
export type ScenarioPreset = z.infer<typeof presetSchema>;

/** What the scenario bar can offer: rule-picked hours, the days that have data, and the hours of the day first shown. */
export type ScenarioOptions = {
  presets: ScenarioPreset[];
  days: string[];
  initialDay: string | null;
  initialHours: string[];
};

export function serializeScenario(scenario: Scenario): string {
  return JSON.stringify(scenario);
}

/** The scenario stored in a cookie value, or null when there is none or it is not a valid one. */
export function parseScenario(value: string | undefined): Scenario | null {
  if (!value) return null;
  try {
    const parsed = scenarioSchema.safeParse(JSON.parse(value));
    return parsed.success ? parsed.data : null;
  } catch {
    return null;
  }
}

export const dayOf = (timestamp: string): string => timestamp.slice(0, 10);
export const hourLabel = (timestamp: string): string => `${timestamp.slice(11, 16)} UTC`;

export function describeScenario(scenario: Scenario): string {
  return `${dayOf(scenario.timestamp)} at ${hourLabel(scenario.timestamp)}, row starting at ${scenario.angle}°`;
}

export function describePresetWeather(preset: ScenarioPreset): string {
  const { ghi_wm2, cloud_cover_pct, wind_gust_kmh } = preset.weather;
  return `${dayOf(preset.timestamp)} ${hourLabel(preset.timestamp)} · sunlight ${Math.round(ghi_wm2)} W/m² · cloud ${Math.round(cloud_cover_pct)}% · gusts ${Math.round(wind_gust_kmh)} km/h`;
}

/** The day the bar opens on: the visitor's own scenario, else the first preset, else the first day with data. */
export function initialDayFor(current: Scenario | null, presets: ScenarioPreset[], days: string[]): string | null {
  const preferred = current ? dayOf(current.timestamp) : presets[0] ? dayOf(presets[0].timestamp) : null;
  return preferred && days.includes(preferred) ? preferred : days[0] ?? null;
}

/** The hour the picker opens on: the visitor's own, else a preset's on that day, else the day's first hour. */
export function initialHourFor(current: Scenario | null, presets: ScenarioPreset[], hours: string[]): string {
  const preferred = [current?.timestamp, ...presets.map(preset => preset.timestamp)].find(timestamp => timestamp !== undefined && hours.includes(timestamp));
  return preferred ?? hours[0] ?? "";
}

/** A message a visitor can act on, from the live API's error code. Internal details are never shown. */
export function scenarioFailureMessage(failure: { status: number | null; code: string | null } | null): string {
  if (failure?.code === "UNKNOWN_HOUR" || failure?.code === "UNKNOWN_DAY") return "That hour is not in the recorded data.";
  if (failure?.code === "RATE_LIMITED") return "The live demo is busy right now. Try again in a minute.";
  if (failure?.code === "INVALID_PAYLOAD") return "The data for that hour did not pass the dashboard's checks. Try another hour.";
  if (failure?.status === 401 || failure?.status === 403) return "Live scenarios are not set up correctly on this site.";
  return "The live service did not answer. It may be waking up; try again in a few seconds.";
}
