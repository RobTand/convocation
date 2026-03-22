FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY convocation/__init__.py convocation/__init__.py
RUN pip install --no-cache-dir .

COPY . .
RUN pip install --no-cache-dir --no-deps .

# Configure git for content repo commits
RUN git config --global user.email "convocation@localhost" && \
    git config --global user.name "ConvocAItion"

EXPOSE 8080

CMD ["uvicorn", "convocation.main:app", "--host", "0.0.0.0", "--port", "8080"]
