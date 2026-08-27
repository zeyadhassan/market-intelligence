# fi-intel

Minimal shell for rebuilding the market-intelligence project in small, reviewable steps.

This branch intentionally contains only:

- Python package metadata with no runtime dependencies
- A small status command that proves the package is runnable
- A dependency-free smoke test

## Run

Requires Python 3.11 or later.

```powershell
python -m fi_intel status
```

Expected output:

```json
{"name": "fi-intel", "stage": "scaffold", "status": "ready", "version": "0.1.0"}
```

## Test

```powershell
python -m unittest discover -s tests -v
```

## Incremental migration rule

Move one coherent capability from the existing implementation at a time. Each migration should
bring its focused tests and documentation in the same commit, while keeping this command and the
full scaffold test suite green.
