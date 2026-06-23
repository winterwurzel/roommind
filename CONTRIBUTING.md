# Contributing to RoomMind

Thanks for your interest in contributing! Here's how to get started.

## Development Setup

### Prerequisites

- Python 3.12+
- Node.js 20+
- A Home Assistant instance for testing

### Backend

```bash
python -m venv .venv
source .venv/bin/activate
pip install pytest pytest-asyncio homeassistant voluptuous
```

### Frontend

```bash
cd frontend
npm install
npm run dev     # Dev server with hot reload
npm run build   # Production build (TypeScript check + Vite bundle)
```

The production build outputs to `custom_components/roommind/frontend/roommind-panel.js`.

## Running Tests

```bash
# All tests
.venv/bin/pytest tests/ -v

# Single test file
.venv/bin/pytest tests/test_store.py -v

# Single test
.venv/bin/pytest tests/test_coordinator.py::test_heating_mode -v
```

Tests are organized by module:
```
tests/
  coordinator/    # Coordinator logic tests
  control/        # MPC, thermal model, solar tests
  managers/       # Manager-specific tests
  services/       # Analytics service tests
  utils/          # Utility function tests
  integration/    # Multi-cycle integration tests
```

## Test Coverage

Coverage must stay ≥ 95% (enforced in CI). Check locally:

```bash
.venv/bin/pytest tests/ --cov=custom_components/roommind --cov-report=term-missing
```

## Code Style

### Python

- Async-first (`async_` prefix for async methods)
- Type hints on all function signatures
- `voluptuous` for WebSocket API validation
- `_LOGGER` for logging (never `print()`)

### TypeScript / Lit

- `@customElement` decorator for web components
- `@property` for HA-bound properties, `@state` for internal state
- HA CSS custom properties only (e.g. `--primary-color`) — never hardcode colors
- Use HA built-in components (`ha-card`, `ha-button`, `ha-select`, etc.)

### i18n

All user-facing strings go through `localize()`. Add new keys to `frontend/src/locales/en.json`, `de.json` and `fr.json`.

## Commit Messages

Use short, one-line messages with a type prefix:

```
feat: add room display name aliases
fix: round humidity display to one decimal
refactor: extract schedule resolution logic
docs: update installation instructions
chore: update dependencies
```

## Pull Requests

1. Fork the repo and create a feature branch from `main`
2. Make your changes
3. Ensure all tests pass and frontend builds cleanly
4. Submit a PR with a clear description of what and why

## Reporting Issues

Use [GitHub Issues](https://github.com/snazzybean/roommind/issues) with the provided templates for bug reports and feature requests.
