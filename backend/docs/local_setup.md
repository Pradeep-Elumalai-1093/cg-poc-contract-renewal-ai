# Run it locally

You need Docker, Python 3.12+ and Node. Commands run from the repo root unless a `cd` says otherwise.

## 1. Start PostgreSQL 18

```
docker compose up -d db
```

The database is `uda_1325` on `localhost:5432` (user and password `postgres`, development only).

## 2. Set up the backend

```
cd backend
python -m venv .venv
.venv\Scripts\activate            # Windows   (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
copy _env.example .env            # macOS/Linux: cp _env.example .env
```

Edit `.env`. For local work the defaults are right except:

```
ENV=development
AUTH_MODE=dev
BOOTSTRAP_ADMIN_EMAILS=you@yourcompany.com     # the first person to sign in becomes admin
```

Create the tables (safe to run again; the app refuses to start until this has been run):

```
alembic upgrade head
```

## 3. Load data

Pick one.

**A. Sample data (no Snowflake needed)**

```
python -m ingest.sample --rows 5000 --out data
python -m ingest --source local --contracts data/contract.csv --claims data/claims.csv
```

**B. An extract you exported from Snowflake** (CSV or Excel, same columns as the Contract data product)

```
python -m ingest --source local --contracts path\to\contract.csv --claims path\to\claims.csv --dry-run
python -m ingest --source local --contracts path\to\contract.csv --claims path\to\claims.csv
```

**C. Straight from Snowflake**

```
pip install -r requirements-ingest.txt
```

Fill in the `SNOWFLAKE_*` lines in `.env` (key-pair auth is best for a scheduled job), then:

```
python -m ingest --source snowflake --dry-run
python -m ingest --source snowflake
```

`--dry-run` reads, validates and prints the report without writing anything, so use it first. The
report shows live vs not-live contracts, segments, each CTX's median and whether it fell back to the
all-CTX median, equipment categories with no crosswalk entry, and any warnings.

What the load does and doesn't do:

- It builds the new data off to the side and switches it live in one short step. If anything fails, the
  current data is exactly as it was. Readers see the old data or the new, never half of either.
- It refuses an extract with far fewer live contracts than the current data (exit code 2; override with
  `--force` if the drop is real), and refuses a second run while one is in progress.
- A missing **required** column stops the load. A missing optional column loads as empty, with a warning.
- Exit codes: `0` loaded, `1` failed, `2` refused.

## 4. Run the API

```
uvicorn main:app --reload --port 8000
```

If it says the database is at the wrong migration, run `alembic upgrade head`. Before step 3 the app runs but
shows no contracts.

## 5. Run the frontend

```
cd frontend
npm install
npm run dev
```

Open http://localhost:5173 (Vite proxies `/api` to the backend on port 8000).

## 6. First sign-in

1. Enter the email you put in `BOOTSTRAP_ADMIN_EMAILS`. You become admin and see all areas.
2. Open **Access**, give people their areas (the sample data has 034, 049, 043, 045, 046) and
   rename areas if you like.
3. Anyone else who signs in sees "Waiting for access" until an admin assigns them an area.

## 7. The daily load

`python -m ingest --source snowflake` is a standalone command: schedule it wherever suits (cron, an
ECS/Fargate scheduled task, a CI job) with the `.env` values as environment variables. Give it at least
4 GB of memory (a 300,000-contract load peaked at about 2.2 GB when tested). It needs
`DATABASE_URL`, the Snowflake settings, and network access to both.

## Notes

- The app never loads the whole book: the worklist is read one page at a time, search/sort/filters run in
  PostgreSQL, and the KPIs and charts are computed there too. Start the API before any data is loaded and it
  runs - the screens are simply empty (the API answers "No contract data has been loaded yet").
- Each user keeps one saved worklist view (columns and sort), changed from the worklist's "columns" picker
  and column headers.
- Contact details (address, phone ...) are loaded but only sent when a user clicks "Show contact details" in a
  contract's Details tab; each request is written to the audit trail.
- A new data load while someone is scrolling shows "New data has been loaded" with a Refresh button.
- Checks (need PostgreSQL running, nothing else): `python tests/check_ingest.py`, `check_worklist.py`,
  `check_schema.py`, `check_access.py`, `check_sso.py`. Point `TEST_PG_ADMIN_DSN` at your server if it isn't
  the compose one.
