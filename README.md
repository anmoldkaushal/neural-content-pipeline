# neural-content-pipeline

A creation-only content pipeline: given a writer's brief, a client's verified knowledge base, a
client note, and a style guide, it produces a gated, reviewed draft -- plus a handful of
short-copy options (titles, hooks, CTAs) alongside it.

## What this is not

Not a research tool (context comes in verified, from a client knowledge base this repo maintains
but does not originate research for), not a publishing/distribution system, not a multi-week
autonomous operator. It runs one job at a time, produces one reviewed package, and stops.

## Stack

Python 3.9+, Pydantic schemas, filesystem-based client folders (no database in v1), a `click`
CLI. LLM calls shell out to the `claude` CLI by default -- see `.env.example`.

## Get started

See `QUICKSTART.md`.
