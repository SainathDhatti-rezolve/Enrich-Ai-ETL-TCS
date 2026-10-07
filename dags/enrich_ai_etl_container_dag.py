"""Cloud Composer 2 DAG — run the Enrich AI ETL as a container.

Deployment approach recommended by the SRE team: build the existing Docker image,
push it to Artifact Registry, and let Airflow run it once per day via the
KubernetesPodOperator. Airflow replaces APScheduler as the scheduler; the pod runs
``run_once.py`` (a single pipeline pass) and exits.

    Project:  etl-lower-92fd
    Region:   us-central1
    Composer: 2  (Airflow 2.x, runs on GKE)
    Image:    us-central1-docker.pkg.dev/etl-lower-92fd/enrich-ai-etl/enrich-ai-etl:latest

Only THIS file needs to go into the environment's /dags bucket — the ETL code and
config are baked into the container image, not shipped to the bucket.

Secrets (replacing .env)
------------------------
The image contains config/config.yaml, whose ${ENV:VAR} placeholders are resolved
at runtime from the pod's environment. We inject those values from Airflow
Variables (Admin -> Variables). Set:

    ETL_SFTP_HOST, ETL_SFTP_USERNAME, ETL_SFTP_PASSWORD  (or ETL_SFTP_KEY_PATH),
    ETL_DOWNLOAD_DIR, ETL_UPLOAD_DIR, ETL_UPLOAD_ENABLED

NOTE: env_vars render the values into the task instance details. Fine for the
lower environment; for production switch to Kubernetes Secrets / Secret Manager.
"""

from __future__ import annotations

import pendulum
from airflow import DAG
from airflow.models import Variable
# The KubernetesPodOperator module was renamed across provider versions:
#   provider >= 7.0.0 :  ...operators.pod
#   provider <  7.0.0 :  ...operators.kubernetes_pod
# Composer 2.5.1 (Airflow 2.6.3) uses the newer "pod" path. Try it first and fall
# back so this DAG works on either provider version.
try:
    from airflow.providers.cncf.kubernetes.operators.pod import (
        KubernetesPodOperator,
    )
except ImportError:  # older provider
    from airflow.providers.cncf.kubernetes.operators.kubernetes_pod import (
        KubernetesPodOperator,
    )

IMAGE = "us-central1-docker.pkg.dev/etl-lower-92fd/enrich-ai-etl/enrich-ai-etl:latest"

# Airflow Variables injected into the container as environment variables. Each
# maps to a ${ENV:...} placeholder in the image's config/config.yaml.
_ETL_VAR_NAMES = (
    "ETL_SFTP_HOST",
    "ETL_SFTP_USERNAME",
    "ETL_SFTP_PASSWORD",
    "ETL_SFTP_KEY_PATH",
    "ETL_DOWNLOAD_DIR",
    "ETL_UPLOAD_DIR",
    "ETL_UPLOAD_ENABLED",
)


def _etl_env() -> dict:
    """Build the pod's env from Airflow Variables (unset ones fall back to the
    config.yaml defaults, exactly like an unset env var would locally)."""
    env = {}
    for name in _ETL_VAR_NAMES:
        value = Variable.get(name, default_var="")
        if value != "":
            env[name] = value
    return env


# Original schedule: 10:00 America/Chicago daily. A pendulum-tz start_date makes
# Airflow interpret the cron in that timezone and handle DST automatically.
LOCAL_TZ = pendulum.timezone("America/Chicago")

default_args = {
    "owner": "TCS",
    "retries": 2,
    "retry_delay": pendulum.duration(minutes=5),
}

with DAG(
    dag_id="enrich_ai_etl_container",
    description="Enrich AI ETL run as a container via KubernetesPodOperator (Composer 2).",
    schedule="0 10 * * *",
    start_date=pendulum.datetime(2026, 8, 1, tz=LOCAL_TZ),
    catchup=False,
    max_active_runs=1,
    tags=["etl", "enrich-ai", "sftp", "container"],
    default_args=default_args,
) as dag:
    run_etl = KubernetesPodOperator(
        task_id="run_etl_container",
        name="enrich-ai-etl",
        # --- Composer 2 required boilerplate (verified against Google docs) ---
        namespace="composer-user-workloads",
        config_file="/home/airflow/composer_kube_config",
        kubernetes_conn_id="kubernetes_default",
        # --- The image + how to run it ---
        image=IMAGE,
        image_pull_policy="Always",  # :latest tag — always fetch the newest push
        # Run the single-run entrypoint (also the image's default CMD now that
        # APScheduler is retired); set explicitly so it's clear what executes.
        cmds=["python"],
        arguments=["run_once.py"],
        env_vars=_etl_env(),
        # Stream the container's stdout/logging into the Airflow task log.
        get_logs=True,
        startup_timeout_seconds=300,
    )
