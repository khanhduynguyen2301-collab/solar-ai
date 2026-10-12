"use server";

import { refresh } from "next/cache";
import { cookies } from "next/headers";

import { loadScenarioData } from "@/lib/frontend-data.server";
import { DAY_PATTERN, SCENARIO_COOKIE, SCENARIO_COOKIE_MAX_AGE_SECONDS, scenarioFailureMessage, scenarioSchema, serializeScenario } from "@/lib/scenario";
import { SolarApiError, apiSettings, fetchDayHours } from "@/lib/solar-api.server";

export type ScenarioActionResult = { ok: true } | { ok: false; message: string };

/**
 * Runs the agents on one recorded hour for this visitor. The scenario is only remembered once the
 * live API has returned a payload the dashboard accepts, so a failed run leaves the pages as they were.
 */
export async function runScenario(input: unknown): Promise<ScenarioActionResult> {
  const parsed = scenarioSchema.safeParse(input);
  if (!parsed.success) {
    return { ok: false, message: "Choose a recorded hour and a row angle between 0 and 90 degrees." };
  }
  if (!apiSettings()) {
    return { ok: false, message: "Live scenarios are not set up on this site." };
  }
  try {
    await loadScenarioData(parsed.data);
  } catch (error) {
    return { ok: false, message: scenarioFailureMessage(error instanceof SolarApiError ? error : null) };
  }
  (await cookies()).set(SCENARIO_COOKIE, serializeScenario(parsed.data), {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    maxAge: SCENARIO_COOKIE_MAX_AGE_SECONDS,
    path: "/",
  });
  refresh();
  return { ok: true };
}

/** Back to the recorded demo. */
export async function resetScenario(): Promise<ScenarioActionResult> {
  (await cookies()).delete(SCENARIO_COOKIE);
  refresh();
  return { ok: true };
}

/** The replayable hours of one day, for the hour picker. Empty when the day is unknown or the API fails. */
export async function listDayHours(day: unknown): Promise<string[]> {
  if (typeof day !== "string" || !DAY_PATTERN.test(day) || !apiSettings()) {
    return [];
  }
  try {
    return await fetchDayHours(day);
  } catch {
    return [];
  }
}
