# Contributing and Release Guide

Internal reference for maintaining and publishing the `semipy` Python package and `semipy-vscode` VS Code extension.

## Repository layout

```
semipy-package/
  semipy/              Python package source
  semipy-vscode/       VS Code extension source
  tests/               pytest tests
  examples/            usage examples (excluded from wheel)
  semipy_testbed/      developer sandbox (excluded from wheel)
  pyproject.toml       Python package metadata and deps
  CONTRIBUTING.md      this file
```

## Development setup

```bash
# Install uv if needed
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install all deps including dev extras
uv sync --all-extras
source .venv/bin/activate

# Copy .env.example and fill in keys
cp .env.example .env   # set OPENAI_API_KEY
```

## Running tests

```bash
pytest tests/unit/          # fast, no LLM
pytest tests/               # all tests (integration tests require OPENAI_API_KEY)
```

Lint:

```bash
ruff check semipy/
```

## Python package release (PyPI)

**1. Bump version** in `pyproject.toml`:

```toml
[project]
version = "0.2.1"
```

**2. Build:**

```bash
python -m build
```

Outputs `dist/semipy-<version>-py3-none-any.whl` and `dist/semipy-<version>.tar.gz`.

**3. Verify:**

```bash
twine check dist/*
```

**4. Publish:**

```bash
# Test PyPI first
twine upload --repository testpypi dist/*
pip install --index-url https://test.pypi.org/simple/ semipy

# Production
twine upload dist/*
```

Requires a `~/.pypirc` with a PyPI API token, or set `TWINE_USERNAME=__token__` and `TWINE_PASSWORD=pypi-...` in the environment.

**5. Tag the release:**

```bash
git tag v0.2.0
git push origin v0.2.0
```

## VS Code extension release (Marketplace)

The extension lives in `semipy-vscode/`. It is built and published with `@vscode/vsce`.

**Pre-requisites (one-time):**

1. Create a publisher account at [marketplace.visualstudio.com/manage](https://marketplace.visualstudio.com/manage).
2. Create a Personal Access Token (PAT) with **Marketplace > Manage** scope at [dev.azure.com](https://dev.azure.com).
3. Run `npx vsce login semipy` and paste the PAT.

**1. Bump version** in `semipy-vscode/package.json`:

```json
"version": "0.2.1"
```

**2. Install deps:**

```bash
cd semipy-vscode
npm install
```

**3. Compile and type-check:**

```bash
npm run check        # TypeScript type check (no emit)
npm run compile      # esbuild bundle to dist/extension.js
```

**4. Preview package contents:**

```bash
npx vsce ls
```

**5. Build the `.vsix`:**

```bash
npm run package      # produces semipy-vscode-<version>.vsix
```

**6. Install locally to verify:**

```bash
code --install-extension semipy-vscode-<version>.vsix
```

Open a Python file with `@semiformal` code and confirm syntax highlighting, slot history tree, and CodeLens appear.

**7. Publish:**

```bash
npm run publish      # vsce publish — requires active vsce login
```

Or with an explicit PAT:

```bash
npx vsce publish --pat <token>
```

## Extension configuration (`semipy.sessionSource`)

For the extension's slot history panel to show commits, `semipy.sessionSource` in VS Code settings must match the resolved path string that `configure(session_source=...)` uses at runtime.

- Scripts at the repo root: leave `semipy.sessionSource` empty (defaults to source file path).
- Scripts under `examples/`: set `"semipy.sessionSource": "${workspaceFolder}/examples"`.
- Jupyter notebooks with `cwd`-based session: set `"semipy.sessionSource": "${workspaceFolder}"`.

## Dependency policy

Core package deps (in `pyproject.toml [project] dependencies`) must be the minimum needed to `import semipy` and run generation:

| Package | Reason |
|---|---|
| `python-dotenv` | `.env` file loading in `agents/config.py` |
| `pydantic-ai` | agent framework in `agents/generator.py` |
| `rich` | terminal and Jupyter output in `agents/console_*.py` |

Everything else is an optional extra. Before adding a core dep, confirm it cannot be a lazy optional import.

Optional extras:

| Extra | Packages | When needed |
|---|---|---|
| `[jupyter]` | `ipywidgets` | Rich inline output in Jupyter notebooks |
| `[pdf]` | `liteparse`, `llama-cloud` | PDF path materialization in slots |
| `[e2b]` | `e2b-code-interpreter` | Sandboxed gist execution |
| `[cocoindex]` | `cocoindex` | Cocoindex vector store integration |
| `[search]` | `firecrawl-py` | Web search tool |
| `[dev]` | `pytest`, `ruff` | Local development |

## Release checklist

- [ ] All unit tests pass: `pytest tests/unit/`
- [ ] Ruff reports zero errors: `ruff check semipy/`
- [ ] `python -m build` succeeds
- [ ] `twine check dist/*` passes
- [ ] Extension type-checks: `npm run check` in `semipy-vscode/`
- [ ] Extension compiles: `npm run compile`
- [ ] `.vsix` installs and works locally
- [ ] `pyproject.toml` version and `package.json` version bumped
- [ ] `semipy-vscode/CHANGELOG.md` updated
- [ ] Git tag pushed
