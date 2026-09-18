FROM python:3.14-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Predownload the background-removal model at build time (~180MB) so the
# first real poster request doesn't stall waiting for it, and so it isn't
# re-downloaded on every redeploy of an ephemeral container filesystem.
RUN python -c "from rembg import new_session; new_session('isnet-general-use')"

COPY app app
COPY scripts scripts
COPY data data

# cache/, output/, app_data/ are created under DATA_DIR at runtime (see
# app/paths.py) - that's where the host's persistent disk should be
# mounted, so poster/photo caches and the SQLite DB survive redeploys.
ENV DATA_DIR=/app/state

EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
