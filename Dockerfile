FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc build-essential \
    libxml2-dev libxslt1-dev zlib1g-dev \
    libffi-dev libssl-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --upgrade pip wheel && pip install -r requirements.txt

COPY . .

EXPOSE 6666
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "6666", "--proxy-headers"]
