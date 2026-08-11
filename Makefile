.PHONY: up down logs build seed migrate fresh ps

# One-command local stack (Postgres + Redis + MinIO + API + Web)
up:
	cp -n .env.example .env || true
	docker compose -f docker/compose.yaml --env-file .env up --build -d
	@echo ""
	@echo "  Web : http://localhost:8080"
	@echo "  API : http://localhost:8000/api/docs"
	@echo "  Login: admin@hermes.io / Hermes@2026"

down:
	docker compose -f docker/compose.yaml down

# Wipe volumes for a clean slate
fresh:
	docker compose -f docker/compose.yaml down -v

logs:
	docker compose -f docker/compose.yaml logs -f api

ps:
	docker compose -f docker/compose.yaml ps

build:
	docker compose -f docker/compose.yaml --env-file .env build

# Run migrations / seed against a running api container
migrate:
	docker compose -f docker/compose.yaml exec api alembic upgrade head

seed:
	docker compose -f docker/compose.yaml exec api python -m app.seed
