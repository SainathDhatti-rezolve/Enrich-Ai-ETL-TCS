# Enrich AI ETL — container image.
# Runs the APScheduler process; the ETL fires only at the configured schedule time.
FROM python:3.12-slim

# Faster, cleaner Python in containers.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=America/Chicago

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source (see .dockerignore for what is excluded).
COPY src/ ./src/
COPY config/ ./config/
# run_once.py: single-run entrypoint. Airflow / Cloud Composer does the
# scheduling and runs this once per scheduled time; it exits when done.
COPY run_once.py ./

# Runtime working directory (also created on demand by the app).
RUN mkdir -p data

# Default: run the ETL once and exit. Cloud Composer overrides this with the
# same command (python run_once.py). APScheduler has been retired — scheduling
# is now handled by Airflow/Composer.
CMD ["python", "run_once.py"]
