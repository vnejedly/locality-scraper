# Agent Operating Guide (AGENTS.md)

This repository currently contains no source files in the workspace root. Use this document as the default operating rules for agentic coding work here. If/when code is added, update the build/lint/test commands and any repo-specific conventions.

## Quick Start (Figure Out The Stack)

Before changing code, detect the project tooling by checking for:

- JavaScript/TypeScript: `package.json`, `pnpm-lock.yaml`, `yarn.lock`, `bun.lockb`
- Python: `pyproject.toml`, `requirements.txt`, `poetry.lock`, `pytest.ini`, `ruff.toml`
- Go: `go.mod`
- Rust: `Cargo.toml`
- Java: `pom.xml`, `build.gradle`
- General: `Makefile`, `Justfile`, `docker-compose.yml`

If present, prefer running commands via existing scripts (e.g. `npm run test`) over ad-hoc invocations.

## Build / Lint / Test Commands

No project-specific commands were found (no build files exist yet). When tooling appears, use the closest matching section below.

### Node (npm/pnpm/yarn/bun)

- Install: `npm ci` (or `pnpm i --frozen-lockfile`, `yarn --frozen-lockfile`, `bun install --frozen-lockfile`)
- Build: `npm run build`
- Lint: `npm run lint`
- Format: `npm run format` (or `npm run fmt`)
- Tests: `npm test` or `npm run test`

Run a single test (common frameworks):

- Jest: `npx jest path/to/file.test.ts -t "test name"`
- Vitest: `npx vitest run path/to/file.test.ts -t "test name"`
- Playwright: `npx playwright test path/to/spec.spec.ts -g "test name"`

Notes:

- Prefer `npm run <script>` if scripts exist; it pins config consistently.
- If the repo uses TypeScript, run typecheck if available: `npm run typecheck`.

### Python

- Create env: `python -m venv .venv` (activate per shell)
- Install:
  - pip: `python -m pip install -r requirements.txt`
  - poetry: `poetry install`
- Lint/format (common):
  - Ruff: `ruff check .` and `ruff format .`
  - Black: `black .`
  - isort: `isort .`
- Tests: `pytest`

Run a single test (pytest):

- Single file: `pytest path/to/test_file.py`
- Single test: `pytest path/to/test_file.py -k "test_name_substring"`
- Single node id: `pytest path/to/test_file.py::TestClass::test_method`

### Go

- Build: `go build ./...`
- Lint (if configured): `golangci-lint run`
- Tests: `go test ./...`

Run a single test:

- Package: `go test ./path/to/pkg`
- One test: `go test ./path/to/pkg -run '^TestName$'`

### Rust

- Build: `cargo build`
- Lint: `cargo fmt --check` and `cargo clippy --all-targets --all-features -D warnings`
- Tests: `cargo test`

Run a single test:

- `cargo test test_name_substring`

### Make/Just (If Present)

- List tasks:
  - Make: `make help` (if defined) or open `Makefile`
  - Just: `just --list`
- Prefer `make test`, `make lint`, `make format`, `make build` if they exist.

## Cursor / Copilot Rules

No Cursor rules found (`.cursorrules`, `.cursor/rules/`) and no Copilot instructions found (`.github/copilot-instructions.md`) in the current workspace.

If those files are later added, treat them as authoritative and copy any key constraints into this document.

## Code Style Guidelines (Default)

These rules apply unless the repository contains language-specific linters/formatters or an established style.

### Formatting

- Prefer an auto-formatter and do not fight it.
- Keep lines reasonably short (target 100 chars unless the project dictates otherwise).
- Use 2-space indentation for JS/TS and JSON; 4-space for Python; tabs or gofmt defaults for Go; rustfmt defaults for Rust.
- Avoid trailing whitespace; keep one newline at EOF.

### Imports

- Group imports: standard library, third-party, local.
- Keep imports sorted and stable (use tooling: isort/ruff, gofmt, rustfmt, eslint sort rules).
- Avoid deep relative imports when a module alias or package boundary exists.
- Prefer explicit imports over wildcard/glob imports.

### Naming

- Use intention-revealing names; avoid abbreviations unless conventional (e.g. `id`, `ctx`).
- Functions/variables: `camelCase` (JS/TS), `snake_case` (Python), `MixedCaps` exported identifiers (Go), `snake_case` (Rust), `PascalCase` for types.
- Types/classes/components: `PascalCase`.
- Constants: follow project norm; default to `SCREAMING_SNAKE_CASE` for true constants.

### Types / Interfaces

- Prefer types that model domain invariants (non-nullable, enums/union types, constrained types).
- Avoid `any` / overly broad types; prefer `unknown` + narrowing (TS) and explicit protocols/ABCs (Python).
- Keep public APIs stable; prefer small, composable interfaces.

### Error Handling

- Fail fast with actionable messages; include context (ids, operation) but avoid secrets.
- Do not swallow errors; if you catch, either handle meaningfully or rethrow with context.
- Prefer typed/domain errors over string matching.
- In async/concurrent code, ensure errors propagate to the caller and tests.

### Logging

- Use structured logs when available; include request/job ids.
- Avoid logging PII, tokens, credentials, or full payloads by default.
- Log at appropriate levels; avoid noisy logs in tight loops.

### Testing

- Write tests for behavior, not implementation details.
- Keep tests deterministic; do not depend on wall-clock time or network unless explicitly integration tests.
- Prefer table-driven tests where they improve coverage and readability.
- When adding a bug fix, add a regression test that fails without the fix.

### Project Hygiene

- Keep diffs focused; do not mix refactors with feature changes.
- Update docs/config alongside code changes when they affect usage.
- If adding dependencies, justify why and prefer existing utilities.

## Agent Workflow (When Code Exists)

- First, read the nearest README/CONTRIBUTING and existing configs.
- Run the fastest checks first (format/lint/typecheck) before full test suites.
- When debugging CI, reproduce locally with the same command and env when possible.
- Prefer minimal, reviewable changes; avoid sweeping reformatting unless requested.
