"use client";
import { useState, useTransition } from "react";
import { Play, RotateCcw, SlidersHorizontal } from "lucide-react";

import { listDayHours, resetScenario, runScenario } from "@/app/actions/scenario";
import { DEFAULT_ANGLE_DEG, MAX_ANGLE_DEG, MIN_ANGLE_DEG, dayOf, describePresetWeather, describeScenario, hourLabel, initialHourFor, type Scenario, type ScenarioOptions } from "@/lib/scenario";

interface ScenarioBarProps {
  /** Null when the live API could not be reached; the bar then says so instead of offering controls. */
  options: ScenarioOptions | null;
  current: Scenario | null;
  notice: string | null;
}

/** Lets a visitor pick a recorded hour and a starting row angle, and run the agents on it. */
export function ScenarioBar({ options, current, notice }: ScenarioBarProps) {
  const [day, setDay] = useState(options?.initialDay ?? "");
  const [hours, setHours] = useState(options?.initialHours ?? []);
  const [timestamp, setTimestamp] = useState(() => initialHourFor(current, options?.presets ?? [], options?.initialHours ?? []));
  const [angle, setAngle] = useState(current?.angle ?? DEFAULT_ANGLE_DEG);
  const [message, setMessage] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  const status = <p className="scenario-status">
    <span className="scenario-mode" data-live={current !== null}>{current ? "LIVE SCENARIO" : "RECORDED DEMO"}</span>
    {current ? describeScenario(current) : "One recorded run. Pick an hour and run the agents yourself."}
  </p>;

  if (!options) {
    return <section className="scenario-bar" aria-label="Scenario controls">{status}<p className="scenario-message" role="status">{notice ?? "Live scenarios are unavailable right now. Reload the page to try again."}</p></section>;
  }

  const run = (scenario: Scenario) => startTransition(async () => {
    setMessage(null);
    const result = await runScenario(scenario);
    if (!result.ok) setMessage(result.message);
  });

  const chooseDay = (nextDay: string) => {
    setDay(nextDay);
    startTransition(async () => {
      const nextHours = await listDayHours(nextDay);
      setHours(nextHours);
      setTimestamp(nextHours[0] ?? "");
      setMessage(nextHours.length === 0 ? "No recorded hours could be loaded for that day." : null);
    });
  };

  const choosePreset = (presetTimestamp: string) => {
    setTimestamp(presetTimestamp);
    if (dayOf(presetTimestamp) !== day) {
      setDay(dayOf(presetTimestamp));
      startTransition(async () => setHours(await listDayHours(dayOf(presetTimestamp))));
    }
    run({ timestamp: presetTimestamp, angle });
  };

  return <section className="scenario-bar" aria-label="Scenario controls" aria-busy={pending}>
    {status}
    <div className="scenario-presets" role="group" aria-label="Preset hours">
      {options.presets.map(preset => <button key={preset.id} type="button" disabled={pending} data-active={current?.timestamp === preset.timestamp} title={describePresetWeather(preset)} onClick={() => choosePreset(preset.timestamp)}>{preset.label}</button>)}
    </div>
    <form className="scenario-form" onSubmit={event => { event.preventDefault(); if (timestamp) run({ timestamp, angle }); }}>
      <label>Day
        <select value={day} disabled={pending} onChange={event => chooseDay(event.target.value)}>{options.days.map(option => <option key={option} value={option}>{option}</option>)}</select>
      </label>
      <label>Hour
        <select value={timestamp} disabled={pending || hours.length === 0} onChange={event => setTimestamp(event.target.value)}>{hours.map(option => <option key={option} value={option}>{hourLabel(option)}</option>)}</select>
      </label>
      <label className="scenario-angle"><span><SlidersHorizontal size={13} aria-hidden="true"/>Row angle now <output>{angle}°</output></span>
        <input type="range" min={MIN_ANGLE_DEG} max={MAX_ANGLE_DEG} step={1} value={angle} disabled={pending} onChange={event => setAngle(Number(event.target.value))}/>
      </label>
      <button type="submit" className="scenario-run" disabled={pending || !timestamp}><Play size={14} aria-hidden="true"/>{pending ? "Running…" : "Run agents"}</button>
      {current && <button type="button" className="scenario-reset" disabled={pending} onClick={() => startTransition(async () => { setMessage(null); await resetScenario(); })}><RotateCcw size={14} aria-hidden="true"/>Recorded demo</button>}
    </form>
    <p className="scenario-message" role="status">{pending ? "Running the agents. The live service can take a few seconds to wake up." : message ?? notice ?? ""}</p>
  </section>;
}
