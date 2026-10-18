FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY . .
RUN pip install --no-cache-dir -e .

EXPOSE 8000
CMD ["uvicorn", "quantdesk.api:app", "--host", "127.0.0.1", "--port", "8000"]
