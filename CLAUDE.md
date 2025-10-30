# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This repository explores AI usage in Engineering Excellence. The project is Python-based and currently in early stages of development.

## Development Setup

This project uses Python. The .gitignore is configured for Python development with support for various package managers (pip, poetry, uv, pdm, pixi).

When setting up the project for the first time, check for dependency management files:
- Look for `requirements.txt`, `pyproject.toml`, `Pipfile`, or similar
- Install dependencies using the appropriate package manager

## Project Structure

The codebase is currently minimal. As the project grows, typical structure would include:
- Source code in a dedicated module directory
- Tests in a `tests/` directory
- Configuration files at the root

## Testing

Once tests are added, look for:
- pytest configuration in `pyproject.toml` or `pytest.ini`
- Test files typically in `tests/` directory or alongside source code with `test_` prefix

Common test commands:
```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_filename.py

# Run with coverage
pytest --cov
```

## Code Quality Tools

The .gitignore includes Ruff cache, suggesting Ruff may be used for linting and formatting.

Typical commands:
```bash
# Format code
ruff format .

# Lint code
ruff check .

# Lint with auto-fix
ruff check --fix .
```
