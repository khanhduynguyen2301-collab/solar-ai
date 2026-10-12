# Documentation

Start with the [project README](../README.md): it has the architecture diagram, the results and the ways to run the demo. The documents here go deeper on one part each.

## Current system

| Document | What it covers |
| --- | --- |
| [api.md](api.md) | The Python inference API: endpoints, request and response shapes, errors, rate limits, settings |
| [control-service.md](control-service.md) | The Java control service: what it stores, the simulated row position, endpoints, the scheduler, settings |
| [operations.md](operations.md) | Training and saving models, CI and delivery, the Docker image, every environment variable, releasing a version |

## Data and pipeline

| Document | What it covers |
| --- | --- |
| [full-dataset.md](full-dataset.md) | The 2023 to 2025 dataset: where it comes from and how to place it |
| [open-meteo.md](open-meteo.md) | The weather provider and how its fields are mapped |
| [luan-processing.md](luan-processing.md) | Validation, transformation and solar-position processing |
| [postgresql.md](postgresql.md) | The pipeline's PostgreSQL storage |
| [../data/mock/README.md](../data/mock/README.md) | Where the mock fixtures come from |
| [../data/example/README.md](../data/example/README.md) | The synthetic example dataset and its formula |

## Contracts and history

| Document | What it covers |
| --- | --- |
| [architecture.md](architecture.md) | The original shared-contract proposal. The data contracts in it still hold; its status notes describe the first day of the project |
| [frontend-validation-handoff.md](frontend-validation-handoff.md) | How the dashboard validates the payload it reads |

## Where things run

```text
dashboard (Next.js, port 3000)      --->  the same inference API, for a visitor's scenario; else one JSON file
control service (Java, port 8080)   --->  inference API (Python, port 8000)  --->  saved models
        |                                          |
        v                                          v
   decision history                     live weather (Open-Meteo), LLM explanations (optional)
   (PostgreSQL or H2)
```

The dashboard calls the inference API when it is given its address and key; otherwise it shows one recorded file. It does not call the control service. The control service calls the inference API; nothing calls the control service except you.
