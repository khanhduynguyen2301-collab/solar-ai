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
| Docker images | Runs after the three above pass. **Backend image:** builds it, starts the container, waits for `/ready`, requests a recommendation, checks an invalid request is rejected with 422, starts a second container that requires an API key and checks it answers 401 without the key and 200 with it, runs a short load test, and runs a one-off file generation. **Control service image:** builds it, starts it with a PostgreSQL container and the key-protected API container, requests an applied decision and a what-if question, checks the simulated row and the totals, checks an invalid request is rejected with 422, then restarts it and checks the history is still there and the container reports healthy |

Tests use the committed mock, example and seasonal-sample data. They never call the LLM or the network. Tests that need a running PostgreSQL skip themselves.

## Delivery

| What | When | Where it goes |
| --- | --- | --- |
| Backend image | Push to `main`, or a `v*` tag, after every job has passed | `ghcr.io/khanhduynguyen2301-collab/solar-ai`, tagged `sha-<commit>` and either `latest` or the version |
| Control service image | Same | `ghcr.io/khanhduynguyen2301-collab/solar-ai-control-service`, same tags |
| Dashboard | Every push to `main` of the fork | Vercel, at https://solar-ai-chi.vercel.app |

The images that are published are the exact ones the container steps just tested, not rebuilds. Pull requests are tested but never published. Publishing uses the workflow's own token; there are no stored secrets.

Published means stored in the registry. The control service is not deployed anywhere. The API is deployed to Google Cloud Run on every push to `main`; see [Hosting the API on Cloud Run](#hosting-the-api-on-cloud-run).

### Releasing a version

```powershell
git tag v0.2.0
git push fork v0.2.0
```

The tag run publishes both images as `:v0.2.0`.

## The backend image

```powershell
docker run --rm -p 8000:8000 ghcr.io/khanhduynguyen2301-collab/solar-ai:latest
```

- Based on `python:3.11-slim` with the CPU build of PyTorch; about 2 GB.
- Trains its models at build time on the committed seasonal sample, so it starts ready. Results from it differ from the full-dataset numbers in the README.
- Runs as an unprivileged user and has a health check on `/ready`.
- Contains no API key and not the full dataset. Pass `-e ANTHROPIC_API_KEY=...` for explanations. To serve full-dataset models, train them locally and mount `models/artifacts` and the dataset as read-only volumes; the README has the exact options.

The image has only ever been built and run in CI. It has not been run on a developer machine.

## The control service image

Built from [`control-service/Dockerfile`](../control-service/Dockerfile) in two stages: a JDK stage packages the jar with the Maven wrapper, and the final image holds only the jar and a Java 21 runtime. It runs as an unprivileged user, has a health check on `/actuator/health`, and contains no database password and no data. The inference API address and the database are given at run time; see [control-service.md](control-service.md#run-it-with-docker).

Like the backend image, it has only been built and run in CI.

## Hosting the API on Cloud Run

**Status: live** at https://solar-ai-api-363580008545.us-central1.run.app since 2026-10-12. `/health`, `/ready` and `/docs` are open; everything else needs the API key. Checked from outside after the first deployment: `/ready` answered, and `/models` and `/recommendations` answered 401 without a key. A request with the key has not been checked by anyone but the key's owner.

How it works:

1. **One-time setup.** In Google Cloud Shell, with a project that has billing enabled selected, run [`deploy/cloud-run-setup.sh`](../deploy/cloud-run-setup.sh). It creates an image repository, a deployer account and a runtime account, keyless sign-in that only this repository's `main` branch can use, and the API's secrets in Secret Manager (a generated `SOLAR_API_KEY` and, optionally, the Anthropic key). It prints six repository variables to add on GitHub. No cloud key is stored in GitHub.
2. **Every push to `main`.** After the images are tested and published, the *Deploy API to Cloud Run* job copies the exact published API image into the project's registry, deploys it, and checks that `/ready` answers and that a request with no key gets 401.

Settings the job applies: 2 GiB memory, 1 CPU, at most one instance, none when idle (so the first request after a pause waits for a cold start), 20 simultaneous requests, public network access with the API key as the only protection.

Things to know:

- **Cost.** Cloud Run and Secret Manager have free allowances that demo traffic stays inside, but a billing account with a card is required. Stored images are billed beyond 0.5 GB; the setup keeps only the two newest, which should cost a few cents a month. Set a budget alert in the billing console.
- **Rate limits are shared.** Behind Cloud Run every caller appears to come from Google's front end, so the per-client limits act as one limit for everyone: 120 requests a minute, 6 for live or explained requests.
- **Recorded mode uses the seasonal sample**, because that is what the image is trained on. Its decisions differ from the full-dataset numbers in the README.
- **Only the API is deployed.** The control service needs a PostgreSQL database and is not hosted.

To read the key callers must send, run `gcloud secrets versions access latest --secret=solar-api-key` in Cloud Shell. To stop deploying, delete the `GCP_PROJECT_ID` repository variable. To take the service down, delete it in the Cloud Run console.

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
| `SOLAR_API_KEY` | API, load test | none | When set, callers must send this key in `X-API-Key` |
| `SOLAR_INFERENCE_API_KEY` | Control service | none | The key it sends to the inference API |
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

- The API's authentication is one shared key, off unless `SOLAR_API_KEY` is set. The control service has none on its own endpoints.
- Only the API is hosted. The control service is not, and the dashboard does not call the hosted API.
- There is no single command that starts every service together.
- No measured test-coverage percentage.
- No security review has been done of the code that accepts outside input.
