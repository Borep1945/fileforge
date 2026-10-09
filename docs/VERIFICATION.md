# Local verification

Verified on 2026-10-09 with Python 3.14.4 on macOS arm64.
CI declares Python 3.11, 3.12 and 3.13; remote matrix results are tracked separately.

| Check | Local result |
| --- | --- |
| `ruff check .` | PASS |
| `ruff format --check .` | PASS |
| `python -m pytest -q` | PASS: 25 passed in 0.07s |
| `python -m fileforge --help` | PASS |
| `python examples/demo.py` | PASS |
| `python -m build --no-isolation` | PASS |
| `python -m fileforge sizes examples/fixtures --limit 2` | PASS |
| `python -m fileforge sort examples/fixtures` | PASS |

## Actual offline demo output

Temporary backup paths are redacted.

```text
Duplicate groups: 1
Size report: {'total_files': 2, 'total_bytes': 36, 'largest': [{'path': 'copy.txt', 'size': 18}, {'path': 'draft_notes.txt', 'size': 18}], 'errors': {}}
Plan: [Rename(source='draft_notes.txt', target='final_notes.txt', sha256='68adc578f362e140e8685b22580a19dcc5f7d7aa289f3a8821d01e363ff93a66')]
Dry run: None
Verified backup: <temporary-directory>/.fileforge-backups/<transaction-id>
Final contents: keep this content
Extension sort plan: [Rename(source='copy.txt', target='txt/copy.txt', sha256='68adc578f362e140e8685b22580a19dcc5f7d7aa289f3a8821d01e363ff93a66'), Rename(source='final_notes.txt', target='txt/final_notes.txt', sha256='68adc578f362e140e8685b22580a19dcc5f7d7aa289f3a8821d01e363ff93a66')]
Sorted files: ['txt/copy.txt', 'txt/final_notes.txt']
```

The demo creates a disposable directory, uses real file operations and verified backups,
then removes the temporary tree. CLI sort smoke verification above is a dry run.

## Dependency advisory check

`pip-audit --local --progress-spinner off --format json` returned exit 0 on the
shared verification environment: 59 package records and no known vulnerabilities
in audited dependencies. Initial bootstrap pip 26.1 advisories were fixed by
upgrading to 26.2.1, with no ignore flags. Unpublished local AgentKernel was skipped.

Python 3.11 GitHub runner bootstrap setuptools advisories were separately found in
remote CI; CI upgrades setuptools to >=83.0.0 alongside pip. FileForge itself uses
only the Python standard library at runtime. Advisory checks are not application
security audits.
