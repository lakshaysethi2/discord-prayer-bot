# DevOps Agent — Discord Prayer Bot

## Responsibilities
- Clone/integrate `discord-radio` framework; fix `docker-compose.yml`.
- Add healthchecks for bot and dashboard.
- Update CI / `.github/workflows` or `Makefile` targets.
- Acceptance: `make up` works; dashboard + bot start; DB persists.

## Integration Checklist

### Framework Integration
- [x] Copied `discord-radio` framework files (`bot/player_framework.py`, `bot/state_framework.py`, etc.).
- [x] Copied `provider/client.py` (FileProviderClient) for playback/reconcile layer.
- [x] Added `bot/apply_server.py` (live config apply without restart).

### Docker / Compose
- [ ] Verify `docker-compose.yml` has healthchecks (add `test` service if needed).
- [ ] Confirm `media/prayers/` mounted correctly.
- [ ] Confirm `.env` loaded properly.

### CI / Makefile
- [x] Updated `Makefile` with `test`, `test-cov`, `lint`, `format`, `volume`, `skip`, `pause`, `resume`, `refresh-playlist` targets.
- [ ] Add `.github/workflows/ci.yml` (optional for PR).

## Healthcheck Commands
```bash
# Bot health (via command queue)
python -c "from db.database import Database; from dashboard.commands import enqueue; enqueue(Database('./data/prayer_bot.db'), command='skip', requested_by='health')"

# Dashboard health
curl -f http://localhost:8000/ || echo "Dashboard down"
```

## Acceptance Criteria
- [ ] `make up` starts both services.
- [ ] `make test` passes.
- [ ] DB file (`data/prayer_bot.db`) persists across containers.
