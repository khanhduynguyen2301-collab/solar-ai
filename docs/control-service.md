# Control service (Java)

A Spring Boot service that sits in front of the [inference API](api.md). It makes no decisions. It asks the inference API for them, stores each one, and tracks the simulated position of the controlled row.

Code: [`control-service/`](../control-service/). Java 21, Spring Boot 4, Spring Data JPA, Flyway, PostgreSQL (or an H2 file database locally).

## What it does

| Responsibility | Detail |
| --- | --- |
| Request decisions | On demand through `POST /api/decisions`, or on an hourly schedule |
| Store history | Every decision is written once and never changed: summary columns for filtering, plus the full payload exactly as the inference API returned it |
| Track the row | Keeps the row's simulated angle, so each applied decision starts where the previous one left it |
| Report | Paged, filterable history; counts by action; one sum over applied rotations |

What it does not do: predict, optimize, check safety, or change any figure. Values are copied from the inference API's reply. A figure the reply does not contain is stored as null, never zero.

## The simulated row position

There is no hardware and no telemetry, so the service keeps its own record of where the row is:

- It starts at the configured angle (35 degrees by default).
- **Applied decision:** a request with no `currentAngleDeg`. The service sends the row's recorded angle to the inference API. If the answer is ROTATE or STOW, the row is recorded as now sitting at the decided angle. A HOLD leaves it where it was.
- **What-if question:** a request with a `currentAngleDeg`. It is stored in history marked as not applied, and the row does not move.

Applied decisions run one at a time, so two requests cannot both start from the same position. That guarantee is an in-memory lock, so it holds for one running instance only.

## Run it

Start the inference API first, then from `control-service/`:

```powershell
.\mvnw.cmd spring-boot:run "-Dspring-boot.run.profiles=local"
```

The `local` profile stores data in a file database under `control-service/data/`, so nothing else needs installing. Without the profile the service expects PostgreSQL. It listens on port 8080. `GET /actuator/health` reports whether it is up.

## Run it with Docker

CI publishes the image from `main`. It needs the inference API and a PostgreSQL database to exist already:

```powershell
docker run --rm -p 8080:8080 `
  -e SOLAR_INFERENCE_URL=http://host.docker.internal:8000 `
  -e SOLAR_DB_URL=jdbc:postgresql://host.docker.internal:5432/solar_farm_ai `
  -e SOLAR_DB_USER=solar_dev -e SOLAR_DB_PASSWORD=your-password `
  ghcr.io/khanhduynguyen2301-collab/solar-ai-control-service:latest
```

The image holds only the jar and a Java 21 runtime, runs as an unprivileged user, and has a health check on `/actuator/health`. It has only been built and run in CI.

## Endpoints

| Method and path | Purpose |
| --- | --- |
| `POST /api/decisions` | Request a decision and store it. Returns 201 with the stored summary and a `Location` header |
| `GET /api/decisions` | History, newest first |
| `GET /api/decisions/latest` | The newest stored decision |
| `GET /api/decisions/latest/payload` | The newest full payload, in the format the dashboard reads |
| `GET /api/decisions/{id}` | One stored decision |
| `GET /api/decisions/{id}/payload` | Its full payload |
| `GET /api/stats` | Totals |
| `GET /api/row` | The simulated row position |

### POST /api/decisions

Body, every field optional (the body itself may be omitted):

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `mode` | `"recorded"` or `"live"` | `"recorded"` | Passed to the inference API |
| `timestamp` | string, up to 40 characters | the inference API's default hour | Recorded hour to replay |
| `currentAngleDeg` | number, 0 to 90 | none | Omit for an applied decision; give a value for a what-if question |
| `explain` | boolean | `false` | Ask for LLM explanations |

```powershell
curl.exe -X POST http://localhost:8080/api/decisions -H "content-type: application/json" -d '{}'
curl.exe -X POST http://localhost:8080/api/decisions -H "content-type: application/json" -d '{\"currentAngleDeg\": 60}'
```

The response and every history item have these fields: `id`, `createdAt`, `trigger` (MANUAL or SCHEDULED), `mode`, `intervalStart`, `controlTargetId`, `currentAngleDeg`, `action`, `targetAngleDeg`, `selectedModel`, `dataStatus`, `safetyPassed`, `energyGainKwh`, `movementCostKwhEquivalent`, `netBenefitKwhEquivalent`, `reason`, `appliedToRow`.

### GET /api/decisions

| Parameter | Meaning |
| --- | --- |
| `action` | Only ROTATE, HOLD or STOW decisions |
| `from`, `to` | Only decisions created at or after `from` and before `to` (ISO timestamps) |
| `page`, `size` | Page number from 0, and page size from 1 to 100 (default 20) |

Returns `items`, `page`, `size`, `totalItems` and `totalPages`.

### GET /api/stats

| Field | Meaning |
| --- | --- |
| `total`, `byAction` | How many decisions are stored, and how many chose each action |
| `netBenefitOfAppliedRotationsKwhEquivalent` | The net benefit the inference API reported, summed over applied ROTATE decisions only. Null when there are none |
| `first`, `last` | When the oldest and newest decisions were stored |

What-if questions and HOLD or STOW decisions are left out of the sum, because no move was made. The sum is of model predictions for a simulated row; it is not measured energy.

## Errors

The same envelope as the inference API: `{"error": {"code": "...", "message": "..."}}`. Nothing is stored and the row does not move when a request fails.

| Status | Code | When |
| --- | --- | --- |
| 404 | `NOT_FOUND` | No decision with that id, or no decision stored yet |
| 404, 405 | `HTTP_ERROR` | Unknown route or wrong method |
| 422 | `INVALID_REQUEST` | A field or parameter is out of range, malformed, or the body is not JSON. The inference API is not called |
| 422 | `REQUEST_REJECTED` | The inference API rejected the request, for example an unknown hour. Its own message is passed through |
| 500 | `INTERNAL_ERROR` | Unexpected failure; details are in the log only |
| 502 | `INFERENCE_INVALID` | The inference API replied with something that is not a usable decision |
| 503 | `INFERENCE_BUSY` | The inference API is rate limiting this service |
| 503 | `INFERENCE_UNAVAILABLE` | The inference API is unreachable, not ready, or timed out |

## Scheduler

Off by default. When `SOLAR_SCHEDULE_ENABLED=true`, the service requests an applied decision on the configured schedule (five past every hour, UTC, by default) and stores it with the trigger SCHEDULED. A run that fails is logged and skipped; the row is unchanged and the next run happens as normal.

In `live` mode every run currently ends in HOLD, because the inference API reports live weather as INVALID (see [api.md](api.md#what-the-two-modes-do)).

## Settings

| Variable | Default | Effect |
| --- | --- | --- |
| `SOLAR_INFERENCE_URL` | `http://localhost:8000` | Where the inference API is |
| `SOLAR_SCHEDULE_ENABLED` | `false` | Turn the scheduler on |
| `SOLAR_SCHEDULE_CRON` | `0 5 * * * *` | When it runs (Spring cron, UTC) |
| `SOLAR_SCHEDULE_MODE` | `live` | The mode it asks for |
| `SOLAR_DB_URL` | `jdbc:postgresql://localhost:5432/solar_farm_ai` | Database |
| `SOLAR_DB_USER`, `SOLAR_DB_PASSWORD` | `solar_dev`, empty | Database login |

In `application.properties`: the row id and starting angle (`solar.row.*`), and the timeouts for calls to the inference API (2 s to connect, 30 s to read).

## Data model

Two tables, created by the Flyway migration in [`V1__decision_history.sql`](../control-service/src/main/resources/db/migration/V1__decision_history.sql):

- **`decision_record`**: one row per decision, with the summary columns and the payload as text. Indexed by creation time and by action.
- **`row_state`**: one row per controlled row, with its simulated angle and a version column for optimistic locking.

## Tests

```powershell
cd control-service
.\mvnw.cmd test
```

37 tests: the decision flow on the real schema with only the inference API replaced; the HTTP client against a local test server, covering the request shape, status mapping and timeouts; the scheduler; and the error handling.

## Limits

- **PostgreSQL is exercised in CI only.** The unit tests and manual runs use H2 in PostgreSQL-compatibility mode. On every CI run the container starts against a real PostgreSQL, applies the migration, stores two decisions and reads them back after a restart; nothing beyond that has been tried on PostgreSQL.
- **Single instance only**, because of the in-memory lock on applied decisions.
- **No authentication**, and it is not hosted.
- **The dashboard does not read it yet.**
