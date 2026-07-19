# Discord Prayer Bot — Docker Compose Controller

.PHONY: up down build logs restart clean

up:
	docker-compose up -d

down:
	docker-compose down

build:
	docker-compose build

logs:
	docker-compose logs -f

restart:
	$(MAKE) down
	$(MAKE) up

clean:
	docker-compose down -v
	docker-compose rm -f
