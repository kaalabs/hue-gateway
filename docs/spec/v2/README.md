# Hue Gateway API v2.0 (published spec)

This directory contains the published v2.0 spec artifacts (served by `make docs-up`).

- OpenAPI contract (codegen-ready): `docs/spec/v2/openapi.yaml`
- Semantic companion (rules + behavior): `docs/spec/v2/semantic.md`
- Architecture + locked decisions baseline: `docs/change_requests/hue-gateway-api-architecture-0v91.md`
- Live gateway OpenAPI (generated, includes `/v1/*` + `/v2/*`): `docs/openapi.json`

Interactive docs:
- `make docs-up`
- Runtime spec (FastAPI): `http://localhost:8081/openapi.json`
- Static v2 contract: `http://localhost:8081/spec/v2/openapi.yaml`
