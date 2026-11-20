COMPOSE ?= docker compose

.PHONY: env build up down restart logs health test lint clean

env:
	@mkdir -p data media
	@test -f .env || cp .env.example .env 2>/dev/null || true

build: env
	$(COMPOSE) build

up: env
	$(COMPOSE) up -d

down:
	$(COMPOSE) down --remove-orphans

restart:
	$(MAKE) down up

logs:
	$(COMPOSE) logs -f --tail=100

health:
	@$(COMPOSE) ps --format 'table {{.Service}}\t{{.Status}}'

test:
	python -m pytest tests/ -q

lint:
	python -m ruff check .

clean:
	$(COMPOSE) down -v --rmi local --remove-orphans
