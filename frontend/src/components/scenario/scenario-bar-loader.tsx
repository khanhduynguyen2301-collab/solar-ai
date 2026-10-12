import { initialDayFor, type Scenario, type ScenarioOptions } from "@/lib/scenario";
import { apiSettings, fetchDayHours, fetchDays, fetchPresets } from "@/lib/solar-api.server";
import { ScenarioBar } from "./scenario-bar";
import "./scenario-bar.css";

/** What the live API has to offer, or null when it cannot be reached right now. */
async function loadOptions(current: Scenario | null): Promise<ScenarioOptions | null> {
  try {
    const [presets, days] = await Promise.all([fetchPresets(), fetchDays()]);
    const initialDay = initialDayFor(current, presets, days);
    return { presets, days, initialDay, initialHours: initialDay ? await fetchDayHours(initialDay) : [] };
  } catch {
    return null;
  }
}

/** Renders nothing on a site that is not connected to the live API, which then behaves as a recorded demo only. */
export async function ScenarioBarLoader({ current, notice }: { current: Scenario | null; notice: string | null }) {
  if (!apiSettings()) return null;
  return <ScenarioBar options={await loadOptions(current)} current={current} notice={notice}/>;
}
