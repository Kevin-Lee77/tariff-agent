FROM node:22-alpine AS console
WORKDIR /console
COPY frontend/package*.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY requirements.prod.lock ./
COPY src ./src
RUN pip install --no-cache-dir -r requirements.prod.lock \
    && pip install --no-cache-dir --no-deps . \
    && useradd --create-home --uid 10001 tariff \
    && mkdir -p /app/data/private && chown -R tariff:tariff /app/data
COPY migrations ./migrations
COPY alembic.ini ./
COPY --from=console /console/dist ./frontend/dist
USER tariff
EXPOSE 18010
CMD ["python", "-m", "uvicorn", "tariff_agent.api:create_app", "--factory", "--host", "127.0.0.1", "--port", "18010"]
