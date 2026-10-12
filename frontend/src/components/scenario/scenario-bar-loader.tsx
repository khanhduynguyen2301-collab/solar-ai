import { initialDayFor, scenarioFailureMessage, type Scenario, type ScenarioOptions } from "@/lib/scenario";
import { SolarApiError, apiSettings, fetchDayHours, fetchDays, fetchPresets } from "@/lib/solar-api.server";
import { ScenarioBar } from "./scenario-bar";
import "./scenario-bar.css";

/** What the live API has to offer, or the reason it could not be loaded, worded for a visitor. */
async function loadOptions(current: Scenario | null): Promise<ScenarioOptions | string> {
  try {
    const [presets, days] = await Promise.all([fetchPresets(), fetchDays()]);
    const initialDay = initialDayFor(current, presets, days);
    return { presets, days, initialDay, initialHours: initialDay ? await fetchDayHours(initialDay) : [] };
  } catch (error) {
    return scenarioFailureMessage(error instanceof SolarApiError ? error : null);
  }
}

/** Renders nothing on a site that is not connected to the live API, which then behaves as a recorded demo only. */
export async function ScenarioBarLoader({ current, notice }: { current: Scenario | null; notice: string | null }) {
  if (!apiSettings()) return null;
  const options = await loadOptions(current);
  // A wrong key and an unreachable service get different wording, so a broken setup can be told from a sleeping API.
  if (typeof options === "string") return <ScenarioBar options={null} current={current} notice={notice ?? options}/>;
  return <ScenarioBar options={options} current={current} notice={notice}/>;
}
