# Operations: training, CI, delivery and settings

How the parts are built, tested, published and configured. For what each service does, see [api.md](api.md) and [control-service.md](control-service.md).

## Training and saving models

```powershell
.\.venv\Scripts\python.exe -m scripts.train_models
```

Trains boosting and the LSTM on the default dataset, scores them on the validation year, and writes `models/artifacts/` (about 24 MB on the full dataset, about 400 seconds on a 16-CPU laptop). The directory is git-ignored. `--skip-lstm` trains boosting only; `--output` chooses another directory.

Retrain when the dataset, the model code or the artifact format changes. The API refuses a saved set from a different format version and tells you to retrain.

The dataset used is the full 2023 to 2025 set when `data/generated/gem_2023_2025/` exists, otherwise the committed seasonal sample. See [full-dataset.md](full-dataset.md).

## CI

One workflow, [`.github/workflows/ci.yml`](../.github/workflows/ci.yml), runs on every pull request, every push to `main`, and every `v*` tag.

| Job | What it does |
| --- | --- |
| Backend tests | Python 3.11, CPU build of PyTorch, the full `unittest` suite |
| Frontend checks | Node 24: type check, lint, Vitest, production build |
| Control service tests (Java) | Java 21: `./mvnw verify` |
| Backend Docker image | Runs after the three above pass. Builds the image, starts the container, waits for `/ready`, requests a recommendation, checks an invalid request is rejected with 422, runs a short load test, and runs a one-off file generation |

Tests use the committed mock, example and seasonal-sample data. They never call the LLM or the network. Tests that need a running PostgreSQL skip themselves.

## Delivery

| What | When | Where it goes |
| --- | --- | --- |
| Backend image | Push to `main`, or a `v*` tag, after every job has passed | `ghcr.io/khanhduynguyen2301-collab/solar-ai`, tagged `sha-<commit>` and either `latest` or the version |
| Dashboard | Every push to `main` of the fork | Vercel, at https://solar-ai-chi.vercel.app |

The image that is published is the exact one the container steps just tested, not a rebuild. Pull requests are tested but never published. Publishing uses the workflow's own token; there are no stored secrets.

The control service is tested in CI but has no image and is not deployed.

### Releasing a version

```powershell
git tag v0.2.0
git push fork v0.2.0
```

The tag run publishes the image as `:v0.2.0`.

## The backend image

```powershell
docker run --rm -p 8000:8000 ghcr.io/khanhduynguyen2301-collab/solar-ai:latest
```

- Based on `python:3.11-slim` with the CPU build of PyTorch; about 2 GB.
- Trains its models at build time on the committed seasonal sample, so it starts ready. Results from it differ from the full-dataset numbers in the README.
- Runs as an unprivileged user and has a health check on `/ready`.
- Contains no API key and not the full dataset. Pass `-e ANTHROPIC_API_KEY=...` for explanations. To serve full-dataset models, train them locally and mount `models/artifacts` and the dataset as read-only volumes; the README has the exact options.

The image has only ever been built and run in CI. It has not been run on a developer machine.

## The dashboard deployment

Vercel builds `frontend/` from the fork's `main`. The pages are static: the payload file is read at build time. The Vercel project sets `SOLAR_FRONTEND_DATA=data/evaluation/demo_recommendation.json`; without it the dashboard shows the mock fixture.

## Environment variables

| Variable | Used by | Default | Effect |
| --- | --- | --- | --- |
| `ANTHROPIC_API_KEY` | Agents, API | none | Enables LLM explanations. Also read from the repository's `.env` |
| `SOLAR_LLM_MODEL` | Agents, API | `claude-haiku-4-5-20251001` | Model for explanations |
| `SOLAR_DATASET_PATH` | Training, scripts | full dataset if present, else the seasonal sample | Dataset to use |
| `SOLAR_DATASET_KIND`, `SOLAR_LABEL_SOURCE` | Training, scripts | from the known dataset | Required with a custom `SOLAR_DATASET_PATH`; provenance is never guessed |
| `SOLAR_MODEL_DIR` | API | `models/artifacts` | Saved models to load |
| `SOLAR_API_RATE_LIMIT_PER_MINUTE` | API | `120` | Requests per client per minute; `0` for none |
| `SOLAR_API_EXTERNAL_RATE_LIMIT_PER_MINUTE` | API | `6` | Same, for live or explained requests |
| `SOLAR_API_CORS_ORIGINS` | API | `http://localhost:3000` | Browser origins allowed |
| `SOLAR_INFERENCE_URL` | Control service | `http://localhost:8000` | Where the inference API is |
| `SOLAR_SCHEDULE_ENABLED`, `SOLAR_SCHEDULE_CRON`, `SOLAR_SCHEDULE_MODE` | Control service | `false`, `0 5 * * * *`, `live` | Hourly automatic decisions |
| `SOLAR_DB_URL`, `SOLAR_DB_USER`, `SOLAR_DB_PASSWORD` | Control service | local PostgreSQL | Its database |
| `SOLAR_FRONTEND_DATA` | Dashboard | the mock fixture | Payload file to show, relative to the repository root |
| `POSTGRES_*` | Data pipeline | see `.env.example` | The pipeline's PostgreSQL storage |

Secrets belong in `.env` at the repository root, which is git-ignored. `.env.example` lists the names without values.

## Ports

| Service | Port |
| --- | --- |
| Dashboard | 3000 |
| Inference API | 8000 |
| Control service | 8080 |
| PostgreSQL | 5432 |

## Notes for this development machine

These apply to the laptop the project was built on, not to the project in general.

- **TLS interception.** Tools that ship their own certificates fail here. Use `pip install --use-feature=truststore`, and for Maven set `MAVEN_OPTS=-Djavax.net.ssl.trustStoreType=Windows-ROOT`. The live Data Agent uses a standard-library weather client for the same reason.
- **Disk space.** The system drive is nearly full, so Maven's downloads are kept on another drive with `MAVEN_USER_HOME=D:/maven-home` and `-Dmaven.repo.local=D:/maven-home/repository`.
- **Docker Desktop does not start** on this machine, which is why the image and PostgreSQL have only been exercised in CI.

## Known gaps

- No authentication on either service, and neither is hosted.
- The control service has no Docker image and has not been run against PostgreSQL.
- There is no single command that starts every service together.
- No measured test-coverage percentage.
- No security review has been done of the code that accepts outside input.
