# Deploying Enrich AI ETL to GCP Cloud Composer

This is a step-by-step runbook for deploying this ETL to **Google Cloud Composer 2**
(managed Apache Airflow). It assumes no prior GCP/Composer experience.

> **Status:** waiting on GCP access (ticket raised). Do Step 0 once access is granted.

---

## The big picture (read this first)

Today this ETL runs as a Docker container using `scheduler.py` (APScheduler) that
must stay running 24/7. We are moving it to **Cloud Composer**, where **Airflow
becomes the scheduler**. The plan (chosen by the client SRE team):

1. Build this project's **Docker image**.
2. Push the image to **Artifact Registry (GAR)** — Google's private image store.
3. Deploy a small **DAG** file that tells Airflow: "every day at 10 AM, run this
   container once." Airflow runs it via `KubernetesPodOperator` and the container
   executes `run_once.py` (one pipeline pass) then exits.

"Deploying a DAG" = **copying one Python file into a Google Cloud Storage bucket.**
The Airflow scheduler watches that bucket and picks it up within ~1-2 minutes.

```
Your laptop                                   GCP (etl-lower-92fd)
-----------                                   --------------------
docker build  ── push image ──▶  Artifact Registry (enrich-ai-etl:latest)
                                          │
dags/..._container_dag.py ── upload ──▶  Composer GCS bucket /dags
                                          │
                                   Airflow (Composer 2) ── daily 10AM ──▶ runs the container
```

---

## Known environment details (from SRE)

| Thing | Value |
|---|---|
| GCP Project ID | `etl-lower-92fd` |
| Region | `us-central1` |
| Composer version | 2 (Airflow 2.x, GKE-based) |
| Artifact Registry image | `us-central1-docker.pkg.dev/etl-lower-92fd/enrich-ai-etl/enrich-ai-etl:latest` (repo `enrich-ai-etl` + image `enrich-ai-etl`) |
| Docker auth host | `us-central1-docker.pkg.dev` |
| Schedule | Daily 09:00 America/Chicago |
| Environment name | **`etl-lower`** (use in place of `ENV_NAME` below) |
| DAGs bucket | `gs://us-central1-etl-lower-7049eee8-bucket/dags` |
| Airflow UI | https://86358b2c3c6a4329a3098adc4b73ea2c-dot-us-central1.composer.googleusercontent.com |

---

## Files involved

| File | Role |
|---|---|
| `run_once.py` | Single-run entrypoint the container runs under Airflow (exits when done). |
| `Dockerfile` | Builds the image; now includes `run_once.py`. |
| `dags/enrich_ai_etl_container_dag.py` | **THE DAG TO DEPLOY.** Runs the container daily via KubernetesPodOperator. |
| `dags/enrich_ai_etl_dag.py` | Plan B (no container; bundles `src/`). Not deployed unless the container path fails. |
| `requirements-composer.txt` | Only used by Plan B. |

---

## Step 0 — Authenticate & find the environment (once access is granted)

These are interactive; run them in your terminal (in Claude Code, prefix with `!`).

```bash
gcloud auth login
gcloud config set project etl-lower-92fd
gcloud auth configure-docker us-central1-docker.pkg.dev

# Confirm you're in:
gcloud auth list
gcloud config get-value project

# Find the Composer environment name (fill ENV_NAME everywhere below):
gcloud composer environments list --locations=us-central1

# Get the Airflow UI URL and the DAGs bucket:
gcloud composer environments describe ENV_NAME --location us-central1 \
    --format="value(config.airflowUri)"
gcloud composer environments describe ENV_NAME --location us-central1 \
    --format="value(config.dagGcsPrefix)"
```

> If any command says "permission denied," access isn't fully granted yet — go
> back to SRE and ask for the `roles/composer.user` (and Artifact Registry
> writer) role on `etl-lower-92fd`.

---

## Step 1 — Build the Docker image (from the project root)

```bash
docker build -t enrich-ai-etl .
```

---

## Step 2 — Push the image to Artifact Registry

```bash
docker tag enrich-ai-etl us-central1-docker.pkg.dev/etl-lower-92fd/enrich-ai-etl/enrich-ai-etl:latest
docker push  us-central1-docker.pkg.dev/etl-lower-92fd/enrich-ai-etl/enrich-ai-etl:latest
```

> If push fails with "repository not found," the GAR repo `enrich-ai-etl` doesn't
> exist yet — ask SRE to create it (they said they would).

---

## Step 3 — Set the SFTP secrets as Airflow Variables

These replace the local `.env`. The container's `config.yaml` reads them at runtime.
Easiest via the **Airflow UI → Admin → Variables**, or via CLI:

```bash
gcloud composer environments run ENV_NAME --location us-central1 variables -- \
    set ETL_SFTP_HOST your.sftp.host
gcloud composer environments run ENV_NAME --location us-central1 variables -- \
    set ETL_SFTP_USERNAME your_user
gcloud composer environments run ENV_NAME --location us-central1 variables -- \
    set ETL_SFTP_PASSWORD your_password
gcloud composer environments run ENV_NAME --location us-central1 variables -- \
    set ETL_DOWNLOAD_DIR /enrich/imports
gcloud composer environments run ENV_NAME --location us-central1 variables -- \
    set ETL_UPLOAD_DIR "/enrichai/EnrichAi Imports"
# Optional: set ETL_UPLOAD_ENABLED false while testing (download+transform, no upload)
```

The exact secret values are the same ones in your local `.env`.

---

## Step 4 — Deploy the DAG (just the one file — code lives in the image)

```bash
gcloud composer environments storage dags import \
    --environment ENV_NAME --location us-central1 \
    --source dags/enrich_ai_etl_container_dag.py
```

---

## Step 5 — Verify

1. Open the Airflow UI (URL from Step 0).
2. Find the DAG **`enrich_ai_etl_container`**. Toggle it **on** (unpause).
3. Click **▶ Trigger DAG** for a manual test run (don't wait for 10 AM).
4. Open the run → task `run_etl_container` → **Logs**. You should see the pipeline
   download → transform → upload messages.
5. Confirm the output CSV landed in the SFTP upload dir.

### Testing tips
- Use **Trigger DAG** to test on demand — no need to change the schedule.
- To test more safely first, set Variable `ETL_UPLOAD_ENABLED=false` so it runs
  download+transform but does NOT push to the client's SFTP. Set back to `true`
  (or remove it) for real runs.
- To change the schedule: edit `schedule=` in the DAG file and re-run Step 4.

---

## After go-live — cleanup (only once Composer is proven working)

- Delete `scheduler.py`, remove `APScheduler` from `requirements.txt`, drop the
  `schedule:` block from `config/config.yaml` (Airflow schedules now).
- **Push this project to a Git repo** — it currently lives only on the local
  machine, which is risky for something running a client workload.

---

## Updating later (normal change workflow)

- **Code change:** rebuild + push the image (Steps 1-2). Because the DAG uses
  `image_pull_policy: Always` on the `:latest` tag, the next run picks it up.
- **Schedule/DAG change:** edit `dags/enrich_ai_etl_container_dag.py`, re-run Step 4.
