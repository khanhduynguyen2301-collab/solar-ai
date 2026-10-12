# Inference API (Python)

The API serves one agent decision per request from models that were trained and saved beforehand. It runs the same Data, Modeling, Optimization and Manager pipeline as `scripts.run_recommendation`; it never trains or re-scores a model.

Code: [`src/api/`](../src/api/). Interactive documentation is served at `/docs` when the API is running.

## Run it

```powershell
.\.venv\Scripts\python.exe -m scripts.train_models                       # once; writes models/artifacts/
.\.venv\Scripts\python.exe -m uvicorn src.api.app:app --port 8000
```

Add `--workers 4` for more throughput; see [Throughput](#throughput). If no saved models are found, the server still starts: `/health` answers, and every other endpoint returns 503 with the reason.

## Endpoints

| Method and path | Purpose | Rate limited |
| --- | --- | --- |
| `GET /health` | The process is up. Always `{"status": "ok"}` | No |
| `GET /ready` | Models are loaded; also says whether recorded hours and explanations are available | No |
| `GET /models` | When the models were trained, on which dataset, their validation metrics, and the selected model | No |
| `GET /hours` | Count, first, last and default of the recorded hours that can be replayed | No |
| `POST /recommendations` | One decision | Yes |

### POST /recommendations

Request body, every field optional:

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `mode` | `"recorded"` or `"live"` | `"recorded"` | Replay a recorded hour from the test window, or fetch the current forecast for the demo site |
| `timestamp` | string, up to 40 characters | the sunniest recorded hour | The recorded hour to replay, exactly as listed by `/hours`. Ignored in live mode |
| `current_angle_deg` | number, 0 to 90 | 35 | Current tilt of the controlled row |
| `explain` | boolean | `false` | Add LLM explanations to the agent log. Slower, and needs an API key on the server |

Unknown fields are rejected.

```powershell
curl.exe -X POST http://localhost:8000/recommendations -H "content-type: application/json" -d '{\"current_angle_deg\": 60}'
```

The response is a `FrontendData` object, the same shape the dashboard reads from a file and the same one validated by `validate_frontend_data` in [`src/common/schema.py`](../src/common/schema.py). The parts most callers want:

| Path | Meaning |
| --- | --- |
| `decision.action`, `decision.target_angle_deg`, `decision.reason` | ROTATE, HOLD or STOW, the angle, and why |
| `optimization` | Current and recommended angle, energy gain, movement cost, net benefit; `null` if optimization did not run |
| `selected_model`, `model_comparison` | Which model predicted, and the validation metrics of all four |
| `candidate_predictions` | Predicted energy at each candidate angle |
| `safety.checks` | Every safety check with pass or fail and its reason |
| `data_agent` | Data status, source, forecast age and issues |
| `agent_log` | What each agent did, including `llm_reasoning` entries when `explain` was true |
| `errors` | Stages that failed; the Manager still decides when one does |

### What the two modes do

- **Recorded** replays one hour from the 2025 test window. The data is reported as DEGRADED because it is a past hour served as the forecast. Needs the dataset the models were trained on to be present; without it this mode returns 503 and live mode still works.
- **Live** fetches the current hour's forecast from Open-Meteo. The provider gives no forecast issue time, so the validation tool reports the data INVALID and the Manager always holds. The LSTM is reported unavailable in this mode, because its stored metrics were measured with look-back weather history that one fetch does not provide.

## Errors

Every error has one shape and never includes internal details:

```json
{"error": {"code": "UNKNOWN_HOUR", "message": "No recorded hour '1999-01-01T00:00:00Z'; hours run from ..."}}
```

| Status | Code | When |
| --- | --- | --- |
| 404 | `UNKNOWN_HOUR` | `timestamp` is not a recorded hour |
| 404, 405 | `HTTP_ERROR` | Unknown route or wrong method |
| 422 | `INVALID_REQUEST` | A field is missing, out of range, of the wrong type, or unknown; or the body is not JSON |
| 429 | `RATE_LIMITED` | Too many requests; the `Retry-After` header says how long to wait |
| 500 | `INTERNAL_ERROR` | Unexpected failure; details are in the server log only |
| 502 | `RECOMMENDATION_FAILED` | The agents could not produce a safe decision; nothing partial is returned |
| 503 | `MODELS_UNAVAILABLE` | No usable saved models; the message says why |
| 503 | `RECORDED_HOURS_UNAVAILABLE` | The models' dataset is not on this server |
| 503 | `EXPLANATIONS_UNAVAILABLE` | `explain` was true but no LLM key is configured |

## Limits and access

- **Rate limits** are per client address over a sliding minute: 120 requests, and 6 for `live` or `explain` requests, which call an outside service (one of them paid). A limit of 0 turns it off.
- **The limiter is in memory, per process.** With several worker processes each one counts separately, and a restart clears it.
- **Browser access (CORS)** is allowed from `http://localhost:3000` only, unless configured.
- **There is no authentication.** Do not expose the API publicly without a gateway or an API-key check in front of it.
- The connecting address is used as the client identity. Behind a proxy, rate-limit at the proxy.

## Settings

| Variable | Default | Effect |
| --- | --- | --- |
| `SOLAR_MODEL_DIR` | `models/artifacts` | Where the saved models are loaded from |
| `SOLAR_API_RATE_LIMIT_PER_MINUTE` | `120` | Requests per client per minute; `0` for no limit |
| `SOLAR_API_EXTERNAL_RATE_LIMIT_PER_MINUTE` | `6` | Same, for `live` or `explain` requests |
| `SOLAR_API_CORS_ORIGINS` | `http://localhost:3000` | Comma-separated origins allowed to call from a browser |
| `ANTHROPIC_API_KEY` | none | Enables `explain`. Read from the environment or the repository's `.env` |
| `SOLAR_LLM_MODEL` | `claude-haiku-4-5-20251001` | Model used for explanations |

## Saved models

`scripts.train_models` writes a directory with the trained boosting and LSTM models, the validation metrics measured at training time, and a manifest that names the dataset and holds a SHA-256 checksum for every file. On load, the API checks the manifest version and every checksum and refuses a set that is incomplete or altered. A loaded model predicts exactly as the trained one did; this is tested.

The metrics the API reports are the ones measured at training time. They are not recomputed, and `/models` says when and on which data they were measured.

## Throughput

Measured on a 16-CPU laptop with the load generator on the same machine, full dataset, LSTM selected, no LLM, 2,000 requests per row, no failures. See `python -m scripts.load_test` and [`data/evaluation/load_test.json`](../data/evaluation/load_test.json).

| Server processes | Simultaneous clients | Requests per second | p50 | p95 | p99 |
| --- | --- | --- | --- | --- | --- |
| 1 | 1 | 42 | 23.6 ms | 25.4 ms | 28.8 ms |
| 1 | 32 | 59 | 535.0 ms | 661.4 ms | 745.5 ms |
| 4 | 1 | 44 | 22.5 ms | 24.8 ms | 26.5 ms |
| 4 | 32 | 156 | 190.0 ms | 244.1 ms | 284.5 ms |

One process tops out near 59 requests per second because the work is Python code that runs on one core at a time. More worker processes raise the ceiling. These are not capacity figures.

To run the load test, start the server with `SOLAR_API_RATE_LIMIT_PER_MINUTE=0`, then run `python -m scripts.load_test`.
