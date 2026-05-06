# hatpin

Semi-deterministic workflow engine for LLM-based agents.

## Installation

```bash
pip install hatpin
# or from source:
pip install "hatpin @ git+https://github.com/yozlet/hatpin.git"
```

## Usage

```bash
python -m hatpin implement --issue <url> [--repo-path <path>]
```

The workflow engine reads `agent.yaml` for LLM configuration, then executes
a semi-deterministic pipeline: gather context from a GitHub issue, create an
implementation plan, write and test code, and submit a PR.

## Documentation

- [Design doc](hatpin/README.md) — workflow engine architecture and rationale
- [Workflow engine plan](docs/workflow-engine.md) — original implementation plan
- [Workflow fixes](docs/workflow-fixes.md) — fixes and refinements

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -x --timeout=10
```

## License

See [corvidae](https://github.com/schuyler/corvidae) for license information.
