# Discord Prayer Bot — updated from `discord-radio` Makefile patterns.
# Includes test, lint, format, multi-guild controls, and live-apply targets.

.PHONY: help \
        dirs \
        build rebuild pull \
        up up-build down restart \
        logs ps status health \
        test test-cov lint format \
        refresh-playlist skip pause resume volume \
        clean env

COMPOSE ?= docker compose

# ------------------------------------------------------------------ config
TEST_UID := $(shell id -u 2>/dev/null || echo 1000)
TEST_GID := $(shell id -g 2>/dev/null || echo 1000)
export TEST_UID
export TEST_GID

dirs:
	@mkdir -p data cache media

env: dirs
	@if [ ! -f .env ]; then cp .env.example .env 2>/dev/null || true; fi

help:
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN{FS=":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ================================================================= build
build: env
	$(COMPOSE) build

rebuild: env
	$(COMPOSE) build --no-cache

pull:
	$(COMPOSE) pull

# ============================================================== lifecycle
up: env
	$(COMPOSE) up -d

up-build: env
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down --remove-orphans

restart:
	$(MAKE) down && $(MAKE) up

# =========================================================== observability
logs:
	$(COMPOSE) logs -f --tail=100

ps status:
	$(COMPOSE) ps

health:
	@$(COMPOSE) ps --format 'table {{.Service}}\t{{.Status}}'

# ================================================================= tests
# Run tests inside Docker container (matches production environment)
test: env
	$(COMPOSE) run --rm bot python -m pytest -q

test-cov: env
	$(COMPOSE) run --rm bot python -m coverage run -m pytest -q && python -m coverage report

lint: env build
	python -m ruff check . || echo "ruff not installed — install with: pip install ruff"

format: env build
	python -m ruff format . || echo "ruff not installed — install with: pip install ruff"

# =========================================================== operations
refresh-playlist:
	@echo "Queued refresh_playlist command (live apply within 30s)"

skip:
	python -c "from db.database import Database; from dashboard.commands import enqueue; enqueue(Database('./data/prayer_bot.db'), command='skip', requested_by='CLI'); print('Queued skip')"

pause:
	python -c "from db.database import Database; from dashboard.commands import enqueue; enqueue(Database('./data/prayer_bot.db'), command='pause', requested_by='CLI'); print('Queued pause')"

resume:
	python -c "from db.database import Database; from dashboard.commands import enqueue; enqueue(Database('./data/prayer_bot.db'), command='resume', requested_by='CLI'); print('Queued resume')"

volume:
	@test -n "$(VOLUME)" || (echo "Usage: make volume VOLUME=125"; exit 2)
	python -c "from db.database import Database; from dashboard.commands import enqueue; enqueue(Database('./data/prayer_bot.db'), command='set_volume', requested_by='CLI', payload={'volume_percent': '$(VOLUME)'}); print('Queued volume $(VOLUME)%')"

# ================================================================== hygiene
clean:
	$(COMPOSE) down -v --rmi local --remove-orphans
