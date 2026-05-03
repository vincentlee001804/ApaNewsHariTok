# Fly.io / local image build — avoids Paketo remote builder (docker.sock) failures.
FROM python:3.12-slim-bookworm

WORKDIR /app

# lxml / psycopg2-binary use wheels on linux/amd64; keep image minimal.
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

CMD ["python", "-m", "src.bot.bot_main"]
