# uv environment for the Lakebase PostgreSQL + pgvector PoC: 2026-10-26

This is an isolated Python 3.12 environment managed by Astral uv, for the upcoming Lakebase PostgreSQL + pgvector proof
of concept. It is limited to `infrastructure/lakebase_poc/`.

**Not done in this task:**
- uv was not initialised at the repository root;
- no existing environment, requirements file or application code was changed;
- nothing connected to Lakebase, Databricks or any database, and no cloud operation was run.

## 1. uv version

| Binary | Version | Use |
|---|---|---|
| `~/.local/bin/uv` (official Astral standalone installer location) | **uv 0.9.27** (b5797b2ab 2026-01-26) | **Used for this project** |
| `/opt/anaconda3/bin/uv` (first on `PATH`, installed inside Anaconda) | uv 0.9.28 (0e1351e40 2026-01-29) | Not used, not changed |

uv was already installed, so nothing was installed or uninstalled. The lock file is compatible with both binaries.

## 2. Python version

**CPython 3.12.12** (`cpython-3.12.12-macos-aarch64-none`), the standalone build managed by uv in
`~/.local/share/uv/python/`.

- **Pins:** `.python-version` holds `3.12`, and `requires-python = "==3.12.*"`, so the lock and the environment cannot
  drift to 3.13 or later.
- **Interpreters left alone:** Anaconda 3.12.2, framework 3.13.1, `/usr/local` 3.14.5 and system 3.9.6 were found and
  not used.
- **Isolation:** `include-system-site-packages = false`.

## 3. Environment path

`procurement-intelligence/infrastructure/lakebase_poc/.venv/`. It was created by uv; its `pyvenv.cfg` records
`uv = 0.9.27`, `version_info = 3.12.12` and `prompt = lakebase-poc`. Size on disk: 53 MB.

**Note:** your shell exports `VIRTUAL_ENV=/opt/anaconda3`. uv ignores it for project commands and uses the project
`.venv`, and says so in a warning. Leave it as it is. Run project commands as `uv run …` from
`infrastructure/lakebase_poc/`, or with `--project infrastructure/lakebase_poc`.

## 4. Packages and versions

| Package | Version | Role |
|---|---|---|
| `psycopg` | 3.3.6 | PostgreSQL driver (runtime) |
| `psycopg-binary` | 3.3.6 | Bundled libpq 18.6 via the `psycopg[binary]` extra, so no system PostgreSQL client is needed (runtime) |
| `pgvector` | 0.5.1 | pgvector types and psycopg adapters: `Vector`, `HalfVector`, `register_vector` (runtime) |
| `numpy` | 2.5.4 | Vector maths and result checks (runtime) |
| `typing-extensions` | 4.16.0 | Dependency of psycopg |
| `pytest` | 9.1.1 | Tests (dev group) |
| `iniconfig` 2.3.1, `packaging` 26.3, `pluggy` 1.7.0, `pygments` 2.21.0 | — | Dependencies of pytest (dev group) |

- **Installed:** 10 packages, all inside `.venv`.
- **Locked:** `uv.lock` resolves 13 entries, adding the project itself plus `colorama` and `tzdata`. Those two are
  Windows-only markers, so they are not installed on macOS.
- **Separate from the shared environments:** this project's numpy 2.5.4 is not used by any existing code. Anaconda and
  `.venv-structured-mcp` keep numpy 1.26.4, which the frozen ML models record.

## 5. Generated configuration files

| File | SHA-256 | Notes |
|---|---|---|
| `infrastructure/lakebase_poc/pyproject.toml` | `f2c5c448…64b4` | From `uv init --bare --no-workspace --python 3.12`. `requires-python` tightened to `==3.12.*`; dependencies added by `uv add` (`psycopg[binary]>=3.3.6`, `pgvector>=0.5.1`, `numpy>=2.5.4`) and `uv add --dev pytest` (`pytest>=9.1.1`) |
| `infrastructure/lakebase_poc/uv.lock` | `efb3254c…a2d3` | The exact, hash-pinned resolution. Commit it |
| `infrastructure/lakebase_poc/.python-version` | `7b55f8e6…d16d` | `3.12` |
| `infrastructure/lakebase_poc/.gitignore` | `12c62647…654b` | `.venv/`, `__pycache__/`, `.pytest_cache/`. uv also wrote `.venv/.gitignore` (`*`) |
| `infrastructure/lakebase_poc/tests/test_offline_environment.py` | `f10b0dc5…9687` | Offline verification, below |

`--no-workspace` keeps this project independent of any future root-level uv workspace. No `pyproject.toml`, `uv.lock` or
`.python-version` existed anywhere in the repository before.

## 6. Verification results

| Check | Result |
|---|---|
| `uv --version` | `uv 0.9.27` |
| `uv lock --check` | Lock is up to date (13 packages) |
| `uv sync --locked` | Succeeds (10 packages audited) |
| Fresh environment from the lock alone (`UV_PROJECT_ENVIRONMENT=<scratch>` + `uv sync --locked`, then the tests) | Installs the same 10 packages; 8 tests pass. The scratch environment was then removed |
| Interpreter used by `uv run` | `…/infrastructure/lakebase_poc/.venv/bin/python3`, Python 3.12.12 |
| Imports | numpy, psycopg (`pq.__impl__ = binary`, libpq 180006), pgvector, `pgvector.psycopg.register_vector`, pytest: all succeed |
| `uv run --locked pytest tests` | **8 passed** |
| Version-control exclusion | Checked with `git check-ignore` in a throwaway repository holding copies of the two `.gitignore` files (the project itself is not a git repository). `.venv/…` is **ignored**; `pyproject.toml`, `uv.lock`, `.python-version` and `tests/` are **tracked** |

**What the 8 tests cover:**
- the interpreter is 3.12;
- psycopg uses its bundled binary libpq, and the pgvector adapter can be imported;
- four known cosine-similarity values: identical, orthogonal, opposite and scaled vectors;
- on 768-dimensional unit vectors (the dimension of the retrieval model `gte-base-en-v1.5`), ranking by inner product
  equals ranking by cosine similarity;
- 768-dimensional `vector` and `halfvec` text encoding round-trips, without any database.

## 7. Existing environments and pipelines unchanged

The project inventory was compared with the post-cleanup inventory of the same day (SHA-256 per file):
- **Added:** only files under `infrastructure/lakebase_poc/`, plus this report.
- **Removed:** nothing.
- **Changed by this task:** nothing. The other `docs/` differences are the cleanup and storage reports from the
  previous task.

Specifically:
- `.venv-structured-mcp/`: every path and size identical; numpy still 1.26.4;
- shared Anaconda Python 3.12.2: numpy 1.26.4 and psycopg 3.3.6 as before; `pgvector` not installed there;
- `requirements.txt`, `structured_agent/requirements.txt`, `structured_agent/mcp_env/requirements*.txt` and
  `.gitignore`: unchanged;
- Bronze/Silver/Gold code, notebooks and data; `discovery/`; `cohort/`; the RAG embeddings, PDFs and processed outputs;
  the approved 35-project manifests; the frozen ML models; MCP tools, guardrails, routing and conversation memory: no
  files touched;
- no PostgreSQL database, Lakebase or Databricks resource was accessed.

## Next step (not started)

Lakebase resource provisioning and the first connection test need separate approval. When that comes:
- **Credentials:** the connection string must come from the environment (never `.env` values printed or committed).
- **Opt-in:** the first live connection should sit behind the same explicit opt-in pattern as the LLM calls.
