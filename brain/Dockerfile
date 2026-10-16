# The Brain worker: `python run_for.py work`, the same command the Windows task ran.
# Secrets are not in the image: brain/.env is mounted read-only by docker-compose.yml.
# Each customer's working folder (customers/<key>/data, logs) lives in a named volume,
# so it survives rebuilds; this image holds only code.

FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /brain
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN useradd --create-home --uid 1000 brain \
 && mkdir -p customers logs data \
 && chown -R brain:brain /brain
USER brain

CMD ["python", "run_for.py", "work"]
