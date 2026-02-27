# Repository Guidelines

## Project Structure & Module Organization
- `bot.py`: main Telegram bot logic (handlers, Sarvam Vision/Chat clients, formatting, retries, safety filters).
- `main.py`: small entrypoint that calls `bot.main()`.
- `deploy/`: deployment scripts and OCI CI/CD setup notes (`remote-deploy.sh`, `remote-rollback.sh`, `OCI_CICD_SETUP.md`).
- `.github/workflows/`: CI and CD pipelines (`ci.yml`, `cd-deploy-ssh.yml`).
- Runtime/config files: `Dockerfile`, `docker-compose.prod.yml`, `requirements.txt`, `.env.example`.
- Local-only artifacts are ignored (`.env`, `plan.md`, OCR dumps like `photo_*.txt`).

## Build, Test, and Development Commands
- Create env and install deps:
  - `python -m venv .venv`
  - `.venv\Scripts\activate`
  - `pip install -r requirements.txt`
- Run locally:
  - `python bot.py` (primary)
  - `python main.py` (entrypoint wrapper)
- Quick validation:
  - `python -m py_compile bot.py main.py` (same compile check used in CI)
- Container build/run:
  - `docker build -t sarvam-telegram-bot:local .`
  - `docker compose -f docker-compose.prod.yml up -d`

## Coding Style & Naming Conventions
- Python 3.11+, 4-space indentation, type hints for new/changed functions.
- Use `snake_case` for functions/variables, `UPPER_SNAKE_CASE` for constants, `PascalCase` for classes/dataclasses.
- Keep handlers focused; extract shared logic into small helper functions.
- Prefer explicit error messages and safe logging (never log tokens/keys).

## Testing Guidelines
- Current repo uses compile/build validation in CI; no dedicated test suite yet.
- For new logic, add `pytest` tests under `tests/` with names like `test_<feature>.py`.
- Prioritize tests for parsing/formatting, retry logic, and prompt-size fallback behavior.
- Before opening a PR, run compile check and a local bot smoke test with a sample image/PDF.

## Commit & Pull Request Guidelines
- Follow existing history style: short, imperative commit subjects (for example: `Harden SSH deploy workflow...`).
- Keep commits scoped (feature/fix/refactor separated when possible).
- PRs should include:
  - what changed and why,
  - risk/rollback notes (if deployment-impacting),
  - verification steps (commands run, sample bot behavior),
  - screenshots or chat output snippets for UX-visible changes.

## Security & Configuration Tips
- Never commit secrets; use `.env` locally and GitHub Secrets in CI/CD.
- Keep `.env.example` updated when adding new config keys.
- Treat OCR/chat payloads as sensitive user data; store minimally and avoid exposing raw logs publicly.
