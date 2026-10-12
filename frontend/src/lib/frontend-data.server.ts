import { readFile } from "node:fs/promises";
import path from "node:path";

import { cookies } from "next/headers";
import { unstable_rethrow } from "next/navigation";
import { z } from "zod";

import { frontendDataSchema } from "../schemas/frontend-data";
import type { FrontendData } from "../types/solar";
import { SCENARIO_COOKIE, parseScenario, scenarioFailureMessage, type Scenario } from "./scenario";
import { SolarApiError, apiSettings, fetchRecommendation } from "./solar-api.server";

const MOCK_FIXTURE_RELATIVE_PATH = [
  "data",
  "mock",
  "sample_full_frontend_data.json",
] as const;

export class FrontendDataLoadError extends Error {
  readonly details?: string;

  constructor(message: string, details?: string) {
    super(message);
    this.name = "FrontendDataLoadError";
    this.details = details;
  }
}

export type FrontendDataLoadResult =
  | {
      ok: true;
      data: FrontendData;
      sourcePath: string;
      /** The visitor's own scenario when the data came from the live API; null for the recorded file. */
      scenario: Scenario | null;
      /** Why the visitor's scenario could not be shown and the recorded file is shown instead; otherwise null. */
      notice: string | null;
    }
  | {
      ok: false;
      error: FrontendDataLoadError;
    };

/** Set to a JSON file (absolute, or relative to the repository root) to show a generated run instead of the mock fixture. */
export const FRONTEND_DATA_PATH_VARIABLE = "SOLAR_FRONTEND_DATA";

/** The payload file the app shows: the configured generated run when one is set, otherwise the mock fixture. */
export function resolveFrontendDataPath(
  environment: Record<string, string | undefined> = process.env,
): string {
  const configured = environment[FRONTEND_DATA_PATH_VARIABLE]?.trim();
  if (!configured) {
    return resolveMockFrontendDataPath();
  }
  return path.isAbsolute(configured)
    ? configured
    : path.resolve(process.cwd(), "..", configured);
}

/** Optional comma-separated list of further generated runs, one per zone, shown beside the main run in the simulation. */
export const ZONE_RUNS_PATH_VARIABLE = "SOLAR_FRONTEND_ZONE_DATA";

function resolveRepositoryPath(configured: string): string {
  return path.isAbsolute(configured)
    ? configured
    : path.resolve(process.cwd(), "..", configured);
}

export function resolveZoneRunPaths(
  environment: Record<string, string | undefined> = process.env,
): string[] {
  return (environment[ZONE_RUNS_PATH_VARIABLE] ?? "")
    .split(",")
    .map((entry) => entry.trim())
    .filter((entry) => entry.length > 0)
    .map(resolveRepositoryPath);
}

export type ZoneRunsLoadResult = {
  runs: FrontendData[];
  /** Why the configured zone runs could not be shown; null when they loaded or none are configured. */
  error: string | null;
};

/** Every configured zone run, each validated like the main payload. If any fails, none are used and the reason is returned. */
export async function loadZoneRuns(
  filePaths = resolveZoneRunPaths(),
): Promise<ZoneRunsLoadResult> {
  try {
    return {
      runs: await Promise.all(filePaths.map((filePath) => loadFrontendData(filePath))),
      error: null,
    };
  } catch (error) {
    const reason =
      error instanceof FrontendDataLoadError
        ? [error.message, error.details].filter(Boolean).join(" ")
        : error instanceof Error
          ? error.message
          : String(error);
    return { runs: [], error: reason };
  }
}

export function resolveMockFrontendDataPath(): string {
  // npm commands are run from /frontend. Keeping the fixture in /data avoids
  // duplicating the shared contract inside the web app.
  return path.resolve(
    process.cwd(),
    "..",
    ...MOCK_FIXTURE_RELATIVE_PATH,
  );
}

export async function parseFrontendData(
  raw: unknown,
): Promise<FrontendData> {
  try {
    return frontendDataSchema.parse(raw);
  } catch (error) {
    if (error instanceof z.ZodError) {
      const details = error.issues
        .map((issue) => {
          const location =
            issue.path.length > 0 ? issue.path.join(".") : "payload";
          return `${location}: ${issue.message}`;
        })
        .join("\n");

      throw new FrontendDataLoadError(
        "SolarAI frontend data failed contract validation.",
        details,
      );
    }

    throw error;
  }
}

export async function loadFrontendData(
  filePath = resolveMockFrontendDataPath(),
): Promise<FrontendData> {
  let text: string;

  try {
    text = await readFile(filePath, "utf8");
  } catch (error) {
    throw new FrontendDataLoadError(
      "SolarAI frontend data file could not be read.",
      error instanceof Error ? error.message : String(error),
    );
  }

  let raw: unknown;

  try {
    raw = JSON.parse(text) as unknown;
  } catch (error) {
    throw new FrontendDataLoadError(
      "SolarAI frontend data contains invalid JSON.",
      error instanceof Error ? error.message : String(error),
    );
  }

  return parseFrontendData(raw);
}

/** The scenario this visitor chose, read from their cookie. Null outside a request, such as in a build or a test. */
export async function currentScenario(): Promise<Scenario | null> {
  try {
    return parseScenario((await cookies()).get(SCENARIO_COOKIE)?.value);
  } catch (error) {
    // Next.js signals "this page depends on the request" by throwing; that must reach the framework.
    unstable_rethrow(error);
    return null;
  }
}

/** One scenario from the live API, validated by the same schema as the recorded file. */
export async function loadScenarioData(scenario: Scenario): Promise<FrontendData> {
  const raw = await fetchRecommendation(scenario);
  try {
    return await parseFrontendData(raw);
  } catch (error) {
    if (error instanceof FrontendDataLoadError) {
      throw new SolarApiError(error.message, null, "INVALID_PAYLOAD");
    }
    throw error;
  }
}

/**
 * What every page shows. With no argument: the visitor's own scenario from the live API when they
 * have chosen one, otherwise the recorded file. If the live API fails, the recorded file is shown
 * with a notice, never an error page. With a path: that file, as before.
 */
export async function loadFrontendDataResult(
  filePath?: string,
): Promise<FrontendDataLoadResult> {
  if (filePath !== undefined) {
    return loadFileResult(filePath, null);
  }
  const scenario = apiSettings() ? await currentScenario() : null;
  if (!scenario) {
    return loadFileResult(resolveFrontendDataPath(), null);
  }
  try {
    return { ok: true, data: await loadScenarioData(scenario), sourcePath: "live API", scenario, notice: null };
  } catch (error) {
    const failure = error instanceof SolarApiError ? error : null;
    return loadFileResult(resolveFrontendDataPath(), `${scenarioFailureMessage(failure)} Showing the recorded demo instead.`);
  }
}

async function loadFileResult(
  filePath: string,
  notice: string | null,
): Promise<FrontendDataLoadResult> {
  try {
    return {
      ok: true,
      data: await loadFrontendData(filePath),
      sourcePath: filePath,
      scenario: null,
      notice,
    };
  } catch (error) {
    if (error instanceof FrontendDataLoadError) {
      return {
        ok: false,
        error,
      };
    }

    return {
      ok: false,
      error: new FrontendDataLoadError(
        "Unexpected error while loading SolarAI frontend data.",
        error instanceof Error ? error.message : String(error),
      ),
    };
  }
}
