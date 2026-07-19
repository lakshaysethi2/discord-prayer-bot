FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt || echo "No requirements.txt; using default packages"

RUN pip install fastapi uvicorn jinja2 sqlite3 # sqlite3 is built-in; jinja2 needed

COPY . .

EXPOSE 8000
