# AGENTS.md — Ghostline

## What this is

Ghostline is a personal stock research app: a FastAPI backend (`backend/`, Python 3.12, Postgres) and one
SwiftUI app for iPhone and Mac (`apple/`, iOS 26 / macOS 26, XcodeGen). It is a research tool for its owner,
not investment advice, and it never places a real trade.

## Layout

| Path | What |
|---|---|
| `backend/` | the API (`ghostline.main`) and the worker (`ghostline.worker`), one image, plain-SQL migrations in `backend/migrations/` |
| `apple/` | `project.yml` (XcodeGen), the app target `Ghostline` (iOS + macOS), the package `GhostlineKit` |
| `VERSION` | the one version of the whole repository (`x.y.z`) |
| `CHANGELOG.md` | one `## [x.y.z]` section per release; the section is the release notes |
| `.github/workflows/ci.yml` | the required checks `backend` and `apps` |
| `.github/workflows/release.yml` | runs on a `v*` tag: archives and uploads both apps to TestFlight |

## Environments

| Environment | Backend | Apps |
|---|---|---|
| staging | `https://ghostline-staging.atwavetech.com` (health `/health`) | Debug builds point here |
| production | `https://ghostline.atwavetech.com` (health `/health`) | Release builds (TestFlight) point here |

`/health` answers 200 only when the database answers and every migration is applied, and names the running
`version` and `commit`.

## The ship rule

1. Branch from `main`, write the test first, make it pass, run the whole suite, raise `VERSION` and write the
   `CHANGELOG.md` section when the change should ship, **open the pull request into `main`, and stop.**
2. The merge robot merges the pull request when every required check is green and the review found nothing
   serious. A title starting with `WIP:`, a draft, or the label `hold` keeps it out.
3. After the merge, staging is deployed and verified by the pipeline. A merge that raised `VERSION` then offers a
   release; **production is the owner's one tap**, never a session's.
4. Never merge, deploy, tag or push to `main` yourself. Pull requests from forks are never merged.

## Rules

- No secret in any file; every setting comes from the environment (`backend/.env.example` lists the keys).
- Synthetic identities only (`qa+<scenario>@example.com`); demonstration data only.
- Money is `numeric`, quantities allow fractions, every market-data row carries `source` and `as_of`.
- Standard GitHub runners only; a documentation-only change skips the heavy steps inside each job.

## Run it locally

See `backend/README.md` and `apple/README.md`.
