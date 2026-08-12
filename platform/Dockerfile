FROM python:3.11-slim

# System deps for tree-sitter native binaries + psycopg2
RUN apt-get update && apt-get install -y \
    gcc g++ git curl \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency spec and install
COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e .

# Copy application code
COPY . .

# Expose FastAPI port
EXPOSE 8000

# Run uvicorn directly (bypasses Windows-specific run.py)
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
