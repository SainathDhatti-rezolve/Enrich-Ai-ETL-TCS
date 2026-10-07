# Enrich AI ETL

A **temporary** migration tool that transforms product-catalog data from the
Legacy Workbench CSV format into the Enrich AI CSV format.

Every run:

1. Connects to an SFTP server.
2. Downloads the four source CSVs into a run-specific working directory.
3. Merges and transforms them into a single Enrich AI CSV.
4. Expands `category_names` into `category_l*` / `category_path*` columns.
5. Uploads the generated CSV to another SFTP directory (optional — see the upload toggle).
6. Deletes the local working files **only after a successful upload**.
7. Leaves all files intact if any step fails.

The original source files on the SFTP server are **never** modified, renamed,
moved, or deleted. See [`FLOW.md`](FLOW.md) for a full call-flow diagram.

---

## Project layout

```
config/config.yaml         # all settings (schedule, SFTP, file names) — nothing hardcoded
src/
  config.py                # typed config loader; ${ENV:VAR:-default} + .env resolution
  sftp_client.py           # paramiko SFTP client (+ tenacity retry) behind a Protocol
  extract.py               # download the four read-only source files
  transform.py             # merge + transform into the base Enrich AI CSV
  category_parsing.py      # pure helpers: parse category_names into levels/paths
  extract_category_levels.py  # post-transform: add category_l*/category_path* columns
  load_and_push.py         # upload the generated CSV
  cleanup.py               # delete per-run working dirs (success only)
  pipeline.py              # orchestrates Extract -> Transform -> Expand -> Upload -> Cleanup
scheduler.py               # the only entrypoint: APScheduler CronTrigger, runs forever
.env / .env.example        # environment values (secrets, paths, schedule, toggles)
Dockerfile                 # container image (default CMD = scheduler)
docker-compose.yml         # convenience wrapper for build/run/logs
FLOW.md                    # end-to-end call-flow diagrams
tests/                     # pytest suite (synthetic CSVs, no real client files)
```

## Requirements

- Python 3.12
- Install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuration

`config/config.yaml` holds the structure; **all values are driven by environment
variables** using `${ENV:VAR:-default}` placeholders, so nothing is hardcoded and
no secrets are committed. Values come from:

- a local `.env` file (auto-loaded via `python-dotenv` for `python scheduler.py`),
  or
- the container environment (Docker `--env-file .env` / Compose `env_file`).

Copy the template and fill it in:

```bash
cp .env.example .env      # then edit .env
```

Environment variables:

| Variable | Purpose | Example / default |
|----------|---------|-------------------|
| `ETL_SFTP_HOST` / `ETL_SFTP_USERNAME` / `ETL_SFTP_PASSWORD` | SFTP connection | — |
| `ETL_SFTP_KEY_PATH` | private key instead of password (optional) | *(blank)* |
| `ETL_DOWNLOAD_DIR` | remote dir with the 4 source files | `/enrich/imports` |
| `ETL_UPLOAD_DIR` | remote dir the output is uploaded to | `/EnrichAi/EnrichAi Imports` |
| `ETL_UPLOAD_ENABLED` | `true` = push to SFTP; `false` = download+transform only, keep files (testing) | `true` |
| `ETL_SCHEDULE_ENABLED` | enable the scheduler | `true` |
| `ETL_SCHEDULE_HOUR` / `ETL_SCHEDULE_MINUTE` | daily run time | `10` / `0` |
| `ETL_SCHEDULE_TIMEZONE` | schedule timezone (DST-aware) | `America/Chicago` |
| `ETL_LOGS_DIR` | set a path to also write a log file; blank = stdout only | *(blank)* |

> Changing `.env` requires recreating the container to take effect (env vars are
> read at container start, not while it runs) — see the Docker section.

Logging goes to **stdout** by default (captured by the terminal, Docker, and
Azure); a log file is written only when `ETL_LOGS_DIR` is set.

## Working directories

Each run creates unique, never-reused directories under `data/` (named with a
UTC, colon-free run id):

```
data/incoming/<YYYY-MM-DD_HH-MM-SS>/   # the 4 downloaded source files (read-only usage)
data/outgoing/<YYYY-MM-DD_HH-MM-SS>/
    raw_ContainerStore_Import_<date>_<time>.csv  # base transform output (intermediate)
    ContainerStore_Import_<date>_<time>.csv      # final, category-expanded CSV (this is uploaded)
```

On a successful upload both directories are deleted. On any failure — or when
`ETL_UPLOAD_ENABLED=false` — they are left in place for inspection/debugging.

## Running

`scheduler.py` is the only entrypoint. It starts APScheduler and runs the ETL
**only at the configured schedule time** — there is no run-once mode:

```bash
python scheduler.py
```

The scheduler runs indefinitely until the process/container stops. To run at a
different time, change `ETL_SCHEDULE_HOUR` / `ETL_SCHEDULE_MINUTE` in `.env`.

## Docker

The scheduler is designed to run inside a container. Build and run:

```bash
docker build -t enrich-ai-etl .
# Runs daily per the .env schedule (the only mode):
docker run -d --name enrich-ai-etl --restart unless-stopped \
  --env-file .env -v "$(pwd)/data:/app/data" enrich-ai-etl
```

Secrets and settings are passed via `--env-file .env` (resolved through the
`${ENV:VAR}` placeholders in `config.yaml`). `data/`, tests, and local artifacts
are excluded from the image (see `.dockerignore`). **Docker Compose is the easier
path — see below.**

### Docker Compose (recommended — shorter commands)

`docker-compose.yml` bundles the build, `.env`, volume, and restart policy:

```bash
# Build + start the scheduler in the background (runs daily per the .env schedule)
docker compose up -d --build

# Watch logs
docker compose logs -f

# Apply changes after editing .env (recreates so new env is picked up)
docker compose up -d --force-recreate

# Stop and remove
docker compose down
```

Note: editing `.env` requires `up -d --force-recreate` (or `down` then `up`) —
environment variables are read at container creation, not while it runs.

## Output format

The output CSV always has this exact column order:

```
external_id, C:Product_ID, groupings, name, description, url, image_url, brand,
category_ids, category_names, buckets, type, last_curation_update, C:BPA_Free,
C:Bullets, C:Eco_Friendly, C:External_id, C:Height, C:Length, C:Made_In_The_USA,
C:Sku_ID, C:Weight, C:Width
```

Transformation rules:

- **groupings** — first `group_id` for the product (blank if none).
- **category_ids** — every `category_id` for the product concatenated with **no**
  delimiter, in the order found in `product_category.csv`.
- **category_names** — one `root > … > leaf` breadcrumb per category, each wrapped
  in double quotes, joined with commas. Written with standard CSV escaping.
- **C:*** columns — extracted from the `custom_fields` JSON blob. The output uses
  a **fixed** custom-field schema; unlisted keys are ignored and missing keys are
  blank.
- **buckets**, **last_curation_update** — intentionally blank; the client has not
  yet defined a source (see `TODO(client)` notes in `transform.py`).

The output filename is `ContainerStore_Import_{date}_{time}.csv`, e.g.
`ContainerStore_Import_20260811_105130.csv`, where `{date}` is `YYYYMMDD` and
`{time}` is `HHMMSS` in the configured schedule timezone (`ETL_SCHEDULE_TIMEZONE`,
default Central). The timestamp is **colon-free** — `:` is illegal/unsafe in
filenames on Windows-based SFTP servers and would corrupt the upload.

### Category expansion (post-transform, pre-upload)

After the base CSV is written, a second step (`src/extract_category_levels.py`,
using `src/category_parsing.py`) appends extra columns derived from
`category_names`, and **this expanded CSV is the one uploaded**:

- **category_l1 … category_lN** — for each hierarchy level, the values every
  breadcrumb in the row has at that level, comma-joined (paths shorter than the
  level are skipped). `N` = the max depth seen across the whole file.
- **category_path1 … category_pathM** — each raw breadcrumb string for the row.
  `M` = the max number of categories any product has.

Example: `category_names = "Kitchen > Utensils > Knives","Sale > Cutlery > Kitchen Knives"`
→ `category_l1 = "Kitchen,Sale"`, `category_l2 = "Utensils,Cutlery"`,
`category_l3 = "Knives,Kitchen Knives"`, `category_path1 = "Kitchen > Utensils > Knives"`,
`category_path2 = "Sale > Cutlery > Kitchen Knives"`.

## Testing

```bash
python -m pytest
```

Tests use synthetic CSVs via `tmp_path` (no real client files) and cover:

- **transform** — single / multiple categories, missing parent category, JSON
  extraction, missing product group, exact output column order, and a byte-exact
  regression lock.
- **category expansion** — `category_l*`/`category_path*` generation.
- **cleanup** — removes only the targeted run dirs, leaves siblings.
- **config** — `${ENV:VAR:-default}` resolution and bool/int coercion.
- **pipeline** — cleanup-on-success / leave-on-failure, the upload toggle, the
  category-expanded upload artifact, and the run-id / output-filename formats.
