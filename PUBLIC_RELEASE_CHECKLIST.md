# Public Release Checklist

Use this as the final pass before making the repository public.

## Privacy and Secrets

- Keep `.env`, `.venv/`, `data/`, `.claude/`, generated reports, slides, and local backup archives out of Git.
- Use `.env.example` for placeholder environment variables only.
- Run a credential scan before publishing and again after any large import.
- If a real credential was ever committed, rotate it and consider rewriting Git history before pushing public.

## Repository Surface

- Confirm whether generated analysis reports in `reports/` should be public or moved out of the repo.
- Confirm whether notes in `New Features/` are intended for public readers.
- Choose and add a license file before publishing if you want others to reuse the code.
- Update `README.md` if the public project scope has changed since it was last written.

## Shareability

- Add a short project description and screenshots only if the dashboard is part of the public pitch.
- Make sure setup commands work in a clean virtual environment.
- Tag a first public release after the repo is clean so future changes have a clear baseline.
