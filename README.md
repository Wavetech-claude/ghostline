# Ghostline

A personal stock research app: a FastAPI backend on a home server and one SwiftUI app for iPhone and Mac.

Ghostline keeps one ledger with three books (real, practice, AI trader), pulls market data from free sources with
every number stamped by its source and time, lets a small AI trader run on practice money against an S&P 500
ghost account, and keeps hard risk rules the AI cannot change. It never places a real trade.

This repository is public because it is also the proof app of an agentic delivery pipeline: pull requests are
merged by a robot once CI and a review pass, staging deploys and verifies itself, and production is one tap.
See `AGENTS.md` for the ship rule and the layout.

Ghostline is a research tool for its owner, not investment advice.

Status: in build (October 2026). No license has been chosen yet; all rights reserved until one is.
