# Rules

## General

- Never consider backwards compatability - this is a new alpha project. Nothing to break.
- Fail fast. Prefer to fail as soon as an error is detected with a clear error message
- Don't "fallback" - this makes code complex and difficult to debug. Instead fail fast if something is wrong
- Similarly, avoid defaults. Let's pass explicit, documented parameters, with known values. I.e. do not use `def fun(*, param1: str, param2: int)`
- Don't write any documentation, README, or other Markdown files. All natural language content will be written by the developer. 

## Project setup

- Python project managed with uv
- The shell is `fish`
- All commands must be run via uv, i.e.: `uv run $COMMAND`
- The project uses git, but you are never required to commit anything
- The unit tests are located in ./tests/

## Python Style

- All code must be PEP8 and pass validation with `uv run ruff check`
- All code must be fully typed and use ty: `uv run ty check`
- Always use Pydantic for types instead of dataclasses
- Avoid vague dict types and prefer either a pydantic BaseModel or at least a TypedDict
- All code must be covered by unit tests and project coverage must always be greater than 90%: `uv run pytest --cov checksmith tests`
- Pytest tests must be written in the function-based style - no test classes please!
- Pytest modules should be laid out mirroring the checksmith package structure. 
- Lean into interfaces - i.e. Protocol and even ABCs where it makes sense
- Avoid args, kwargs etc whenever possible
- The command line application must use the typer library