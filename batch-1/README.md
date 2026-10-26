# QC Report Automation

A Python/NiceGUI application for preparing, reviewing, approving, and tracking audit reports. It uses SQLAlchemy async sessions, PostgreSQL, Alembic, Microsoft Entra authentication, and Microsoft Graph notifications.

## Included

- Complete application source, static assets, and database migrations.
- Seven ready-to-use Word report templates and their manifest.
- Original controlled Word master and the 26-question Excel workbook.
- Windows setup/start commands and configuration examples.

Local credentials, database contents, generated reports, virtual environments, caches, logs, tests, and deployment-specific tools are excluded from this focused source package.

## Run locally on Windows

Install Python 3.11+ (with the Python launcher or PATH enabled) and PostgreSQL 17 binaries. Extract or clone this repository into a normal folder, open a terminal there, and run:

```powershell
.\setup.cmd
.\start.cmd
```

Setup needs internet access to install Python dependencies. It creates `.venv`, generates a local storage secret and local settings, verifies the seven templates, prepares the local PostgreSQL database, and applies migrations. A new cluster uses an available loopback port starting at `5434`. Existing valid local settings are preserved. Close the application before rerunning setup.

Fresh copies use explicitly enabled loopback demo authentication and do not need a Microsoft registration. The app normally opens at `http://localhost:8080`. PowerShell environment activation is unnecessary.

For connected Microsoft authentication, fill in your own Entra settings in `.env.local` using `.env.example`, set `QC_ENABLE_DEMO_AUTH=false`, and register the Web redirect URI `http://localhost:8080/auth/callback`. Your organisation must supply the Microsoft registration, permissions, and application user/role assignments. Graph email and directory access require their own Microsoft permissions.

Do not commit `.env.local`, `.env.postgres.local`, credentials, database files, or generated client reports. The included `.gitignore` excludes them. Existing reports are not transferred by this package; a newly provisioned database starts empty.

## Templates

The seven templates and manifest are copied unchanged from the application sources, including the original report wording and signatory reference data. Normal use does not require regenerating them. If the controlled Word master is explicitly changed, regenerate the catalog with:

```powershell
.\.venv\Scripts\python.exe -m scripts.extract_report_templates
```

The extractor expects the supplied master format; arbitrary Word files are not interchangeable with it. The question workbook has no report identifiers, so its 26 questions are shared across reports.

## GitHub upload

Upload the contents of this folder as repository files. If using the ZIP, extract it first. This package contains configuration placeholders rather than live credentials.
