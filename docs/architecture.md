# Architecture / shared-contract proposal 0.1.0

> **Historical document.** This is the proposal the team agreed on the first day. The data contracts it defines
> (the 13 weather columns, `FrontendData`, the tool and agent interfaces, the decision priority) are still what the code
> implements. Its status notes are not current: the agents, models, orchestrator, API and dashboard now exist.
> For the system as it is today, see the [project README](../README.md) and the [documentation index](README.md).

Status at the time of writing: **awaiting team approval**. This phase creates boundaries, fixtures,
stubs and contract tests. It does not implement the application. No agent is
running, no ML library is installed, and no API or hardware command is sent.

## Repository inspection and minimal changes

The starting repository contained `README.md`, `.gitignore`, empty
`.env.example`, empty `requirements.txt`, a local Python 3.11.9 `.venv`, and
Git on clean `main`. Origin points to `luanhoangle05/solar-ai`. There was no
existing application, schema, model, frontend, or test structure to preserve
or replace. The initial setup commit was `4c57d79`.

The changes add the requested package/directory skeleton and four shared
contract modules, four canonical mock files, and stdlib contract tests.
Small additions are this review guide, mock provenance, and a reproducible
fixture generator. Existing README content is preserved and extended;
`.env.example` and `requirements.txt` receive explanatory comments only.
`.gitignore` remains unchanged. `data/raw/` exists locally but is ignored;
`data/processed/.gitkeep` preserves the otherwise empty processed directory.

## Architecture and ownership

Data Agent -> Modeling Agent -> Optimization Agent -> Manager / Safety Agent
-> recommendation service -> frontend.

The orchestrator is a lightweight state-machine interface, not a fifth LLM
agent. Tools perform work; agents coordinate tools. The system never creates
one LLM agent per panel. This proposal controls one row per recommendation.
The farm snapshot contains every row so the dashboard can show the full farm.

| Owner | Files / responsibility |
| --- | --- |
| Luan | `src/pipeline/`, `src/models/baseline/`, `src/agents/data_agent.py` |
| Duy | `src/models/advanced/`, evaluation, cost simulation, optimizer, Modeling/Optimization/Manager agents, recommendation service |
| Tung | `src/frontend/`, all dashboard visualization |
| All three | Shared contracts, orchestrator integration and contract changes |

## Resulting file tree

```text
solar-farm-ai/
|-- .env.example                         # comments only
|-- .gitignore                           # unchanged
|-- README.md
|-- requirements.txt                     # stdlib-only for this phase
|-- data/
|   |-- raw/                             # local, ignored by Git
|   |-- processed/
|   |   `-- .gitkeep
|   `-- mock/
|       |-- README.md                    # MOCK provenance and units
|       |-- sample_weather.csv
|       |-- sample_model_output.json
|       |-- sample_full_frontend_data.json
|       `-- agent_state.json
|-- docs/
|   `-- architecture.md
|-- scripts/
|   `-- build_mock_fixtures.py
|-- src/
|   |-- __init__.py
|   |-- common/
|   |   |-- __init__.py
|   |   |-- schema.py
|   |   |-- config.py
|   |   |-- tool_contracts.py
|   |   `-- agent_contracts.py
|   |-- pipeline/
|   |   |-- __init__.py
|   |   |-- client.py
|   |   |-- validate.py
|   |   |-- transform.py
|   |   |-- solar_position.py
|   |   |-- storage.py
|   |   `-- run_pipeline.py
|   |-- models/
|   |   |-- __init__.py
|   |   |-- baseline/
|   |   |   |-- __init__.py
|   |   |   |-- linear_regression.py
|   |   |   `-- random_forest.py
|   |   |-- advanced/
|   |   |   |-- __init__.py
|   |   |   |-- boosting.py
|   |   |   `-- lstm.py
|   |   |-- evaluation.py
|   |   |-- cost_simulation.py
|   |   `-- optimizer.py
|   |-- agents/
|   |   |-- __init__.py
|   |   |-- data_agent.py
|   |   |-- modeling_agent.py
|   |   |-- optimization_agent.py
|   |   |-- manager_agent.py
|   |   `-- orchestrator.py
|   |-- service/
|   |   |-- __init__.py
|   |   `-- recommendation_service.py
|   `-- frontend/
|       |-- __init__.py
|       `-- app.py
`-- tests/
    |-- __init__.py
    |-- test_contracts.py
    |-- pipeline/__init__.py
    |-- models/__init__.py
    |-- agents/__init__.py
    |-- frontend/__init__.py
    `-- integration/__init__.py
```

`.git/` and the ignored `.venv/` remain in place. Feature modules contain
ownership/TODO docstrings. Agent and service entry points explicitly raise
`NotImplementedError`; they cannot silently pretend to produce results.

## Luan -> Duy: weather/training contract

Canonical input: `data/mock/sample_weather.csv`. `WeatherRow` and
`WEATHER_COLUMNS` in `schema.py` define these exact 13 columns:

```text
timestamp,temperature_c,cloud_cover_pct,precipitation_mm,wind_speed_kmh,
wind_gust_kmh,ghi_wm2,dni_wm2,dhi_wm2,sun_elevation_deg,sun_azimuth_deg,
panel_angle_deg,actual_kwh
```

`timestamp` is an ISO-8601 timestamp with a timezone; fixture timestamps use
UTC. It is the beginning of the one-hour target interval. Weather values
represent the same interval. The future pipeline must document aggregation
and how gusts are chosen; sustained wind and gust cannot be interchanged.
Sun azimuth is clockwise from north in [0, 360); elevation is [-90, 90].
Panel tilt is measured from horizontal, nominally [0, 90].

The target `actual_kwh` is energy over that hour for one 20-panel row.
It is never passed to `EnergyPredictor.predict_kwh`. `WeatherFeatures`
contains weather/solar position/current angle/timestamp only. Adapters
substitute each candidate panel angle for prediction; optional hour and
day-of-year features are derived consistently from timestamp. The CSV is
for labeled training/evaluation input. Live forecast inference does not
require a not-yet-observed `actual_kwh`.

Mock CSV provenance is in the adjacent README rather than an extra CSV
column. Later measured and physics-derived label sources must be explicitly
identified in dataset provenance and output metadata. All four models must
use the same target, units and chronological evaluation windows. Do not mix
future and past randomly or fit transforms on held-out future data. For LSTM,
build history windows without reaching beyond the prediction cutoff and
evaluate on timestamps also available to the baseline models. Framework and
sequence length remain Duy's implementation choices.

## Duy -> Tung: final recommendation contract

Canonical input: `data/mock/sample_full_frontend_data.json`, typed as
`FrontendData`. Tung can load it directly; no backend execution is required.

| Section | Meaning |
| --- | --- |
| `timestamp`, `metadata` | Output time, schema version, mock/label provenance, interval, row scope, target row and config identifier |
| `current_weather` | The eight requested weather values with unit suffixes; null if unavailable |
| `data_agent` | VALID/DEGRADED/STALE/INVALID, source, age, cache use, issues |
| `model_comparison` | Exactly four stable model IDs with implementation name, status, MAE/RMSE/R2 |
| `selected_model` | `linear_regression`, `random_forest`, `boosting`, or `lstm`; null if none usable |
| `candidate_predictions` | Unique `{angle_deg, predicted_kwh}` entries, including the stay/current angle |
| `optimization` | Current/proposed angle, baseline, prediction, gain, movement cost, net benefit; null when unavailable |
| `safety` | Aggregate result, named deterministic check results/severity, explanation |
| `decision` | ROTATE/HOLD/STOW, final target angle and reason |
| `farm_status` | Four zones, all 50 rows, 1000 panels; row membership, angle, current state and action |
| `agent_log`, `history`, `errors` | Illustrative activity, previous decisions, and explicit failures |

The key `boosting` is proposed here because the architecture permits XGBoost
or LightGBM. The mock `implementation` is `xgboost`; using LightGBM later
changes that value rather than the shared model ID. R2 may be negative.
Unavailable models have `status: UNAVAILABLE` and null metrics, never zero
as a stand-in for an untrained model. No confidence score is invented from R2.

On an early failure, the payload can still present weather/model absence,
four unavailable model entries, empty candidates, null optimization, errors,
and a real safety decision (HOLD or STOW). The tests include this variant.
Display null results as unavailable. Do not graph them as zero-energy results.

The recommendation applies only to `metadata.control_target_id`. A row's
`angle_deg` describes the current snapshot; a requested angle is not proof
that movement occurred. The frontend must prominently label MOCK mode.

## Agent -> Tool contracts

`tool_contracts.py` defines synchronous Python `Protocol` interfaces:

| Protocol | Consumer | Responsibility |
| --- | --- | --- |
| `WeatherTools` | Data Agent | Fetch, validate, transform, enrich, cache/load/store |
| `ModelingTools` | Modeling Agent | Evaluate all four adapters, select a model, return its predictor |
| `EnergyPredictor` | Modeling tools/agent | Predict per-row kWh for candidate angles in input order |
| `OptimizationTools` | Optimization Agent | Generate candidates including stay, cost movement, calculate net benefit, optimize |
| `SafetyTools` | Manager Agent | Deterministic checks and rules; simulation-only dispatch by default |

The contracts are injected through agent constructors. A mock object and a
real implementation expose the same method names, arguments and return
types. `ToolError` is the shared failure type. `RawWeather` permits nulls to
represent missing provider observations; validated inference features do not.
`forecast_issued_at` distinguishes forecast age from download time. Fetching
old data again must not reset its age. Unknown issue time is a quality issue:
report null `forecast_age_minutes` with STALE/INVALID status and an issue,
rather than inventing zero age. Such a report cannot authorize ROTATE.

Optimization normally consumes the Modeling Agent's predictions, so it does
not depend on any specific ML library. Luan's baseline adapters implement
the same `EnergyPredictor` contract as Duy's advanced adapters. Prediction
is synchronous for this phase; asynchronous scheduling is not required.

## Agent -> Agent state and ownership

`AgentState` contains `run_id`, `timestamp`, metadata and lifecycle stage,
plus `weather`, `data`, `modeling`, `optimization`, `safety`, `decision`,
`agent_log`, `tool_calls`, and `errors`.

| Writer | Owned section returned to orchestrator |
| --- | --- |
| Data Agent | `data`, `weather` |
| Modeling Agent | `modeling` (comparison, selected metrics and predictions) |
| Optimization Agent | `optimization` |
| Manager / Safety Agent | `safety`, `decision` |
| Orchestrator | Run metadata/stage, merge returned sections, append traces/errors |

Agents receive state as read-only by convention and return typed owned-section
updates. A future orchestrator must supply isolated snapshots rather than
allow agents to mutate another owner's section. Trace entries are returned
with each update and appended centrally. Stub methods make no mutations.

Unrun sections are null. This is a deliberate clarification of the example
state: the whole section is null instead of a collection of invented zeros
or partly filled fields. The fixture demonstrates a completed run. On an
upstream failure, the future state machine routes to Manager/Safety with
unavailable results; it must not bypass safety to complete the normal path.
`COMPLETE` means a final safe decision exists, not necessarily that all models
ran. `FAILED` requires an error record.

## Configurable economics and deterministic safety

`config.py` labels its coefficients **PROTOTYPE SIMULATION ASSUMPTIONS**.
Defaults are per row: motor 0.002 kWh/degree, wear 0.001 kWh-equivalent/degree,
minimum move benefit 0.02 kWh-equivalent, max forecast age 30 minutes,
wind 50 km/h, gust 70 km/h, nominal angle bounds 0..90 degrees, stow 0 degrees.
These are proposed simulation values, not measured hardware calibration or
certified safety limits. Nondefault configurations need a distinct config ID.

```text
movement_degrees = abs(target_angle_deg - current_angle_deg)
movement_cost_kwh_equivalent = movement_degrees *
    (motor_kwh_per_degree + wear_kwh_equivalent_per_degree)
energy_gain_kwh = candidate_predicted_kwh - stay_predicted_kwh
net_benefit_kwh_equivalent = energy_gain_kwh - movement_cost_kwh_equivalent
```

Wear is already expressed in energy-equivalent units. Currency estimates are
deferred until the team agrees on a conversion/tariff; dollars cannot be
subtracted directly from kWh. All candidates must cover the same row and
interval. Later system metrics must come from logged outputs and cannot
multiply one illustrative row's benefit by the whole farm without justification.

The future optimizer chooses maximum net benefit. Include the stay candidate
with zero movement cost. Proposed ties choose the least movement, then the
lowest angle for reproducibility. The Manager applies this priority:

1. Severe violation: STOW to configured safe angle.
2. Unreliable/stale data or a failed nonsevere check: HOLD.
3. Best net benefit <= configured threshold: HOLD.
4. Otherwise: ROTATE to the optimizer recommendation.

These are deterministic rules. No LLM can override them. Schema validation
rejects contradictory reports but is **not** the actual safety controller;
`check_wind_safety`, actuator limits, fallback decisions and dispatch still
require implementation and behavior tests. Actual hardware dispatch is out
of this phase. In particular, the illustrative passing mock checks must not
be treated as authorization to operate equipment.

## Tests and limits of current coverage

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

No PowerShell environment activation or package installation is necessary.

| Requested coverage | Architecture-phase status |
| --- | --- |
| Shared schema validation | Implemented: shape, enums, nullability, units/ranges, timestamps, finite numbers and extra/missing fields |
| Weather validation | Implemented for shared CSV rows; provider cleaning/validation deferred |
| Missing/stale data | Contract rejection and nullable safe-output representation covered; runtime routing deferred |
| Cache fallback | Cache provenance can be represented; fetch/retry/cache behavior deferred |
| Baseline and advanced model smoke tests | Deferred until model adapters exist; no fake training tests |
| Model comparison | Four-model structure and synthetic metric arithmetic covered; real time-aware evaluation deferred |
| Maximum-net-benefit optimizer | Fixture illustrates and verifies the distinction; optimizer behavior deferred |
| HOLD below threshold / STOW on violation | Contradictory final payloads rejected; deterministic controller behavior deferred |
| Agent tool interface | Mock predictor signature and stub protocol conformance covered |
| Shared state | Null pending stages, required final decision, errors, cross-fixture consistency covered |
| Duy -> Tung contract | Standalone frontend JSON, farm membership, errors and nullable unavailable results covered |
| End-to-end mock workflow | Deferred until real mock-backed agents/orchestrator are implemented; fixture agreement is not execution |

The fixture generator is only a reproducibility tool. It does not train
models, run an optimizer, run agents, issue control commands or provide an
end-to-end application. Separate behavior tests belong in the existing
`tests/pipeline`, `models`, `agents`, `frontend`, and `integration` packages.

## Assumptions for approval and next phase

No technical blocker prevents approving this scaffold. Confirm the proposed
one-hour, per-row energy scope; tilt-angle convention; stable boosting ID;
whole-row zone grouping; and prototype economic/safety coefficients.
Four equal 250-panel zones cannot each contain only whole 20-panel rows,
so the proposed four zones have 13/12/13/12 rows (260/240/260/240 panels).
Equal zones would require splitting rows or changing the farm layout.

Real-site latitude/longitude, weather provider, panel geometry/capacity,
actuator limits, tariff/wear calibration, label source, model acceptance
criteria and LSTM history length remain implementation decisions. They do
not block contract review or Tung's mock-driven dashboard development.

After approval, commit and push the shared contracts to `main`, then create
these branches from that same approved commit:

```text
feature/luan-data-baselines
feature/duy-modeling-agents
feature/tung-frontend
```

Luan can implement the pipeline and baseline adapters, Duy can build a
mock-backed agent/optimization/safety vertical slice against the CSV, and
Tung can build the dashboard against the full JSON concurrently. First
integration target: CSV -> mock-backed stages -> deterministic decisions ->
validated frontend JSON, with actual end-to-end tests. Real models and real
weather tools replace adapters within the approved interfaces.

Do not rename shared fields unilaterally. Contract changes require joint
review, a schema-version decision, fixture updates, and passing contract
tests before teammates adopt them. This phase stops before implementation,
committing/publishing contracts, or creating teammate branches.
