# Fitted — local dev helpers
# Requires: SSH tunnel open (make tunnel) and venv active
# To offload CLIP to local GPU: make embed-server  (then in separate terminal: make tunnel-embed)

# Detect Windows (Git Bash / MINGW) vs Unix
ifeq ($(OS),Windows_NT)
  PYTHON := .venv/Scripts/python
else
  PYTHON := .venv/bin/python
endif
# Fetch DB URL from SSM at runtime and rewrite host to localhost (SSH tunnel)
_SSM_URL := $(shell aws ssm get-parameter --name "/fitted/database-url" --with-decryption --region us-west-1 --query "Parameter.Value" --output text 2>/dev/null)
DB_URL   := $(shell echo "$(_SSM_URL)" | sed 's|@.*:|@localhost:|')
RUN      := USE_SSM=true DATABASE_URL="$(DB_URL)" PYTHONPATH=. $(PYTHON)

# ── Tunnel ────────────────────────────────────────────────────────────────────

EC2_HOST ?= fitted

.PHONY: tunnel tunnel-embed embed-server

tunnel:
	ssh $(EC2_HOST) -N -L 5432:localhost:5432

tunnel-embed: _wait-embed-server
	ssh -o ExitOnForwardFailure=yes -R 8002:localhost:8002 $(EC2_HOST)

embed-server:
	$(PYTHON) scripts/embedding_server.py $(ARGS)

_wait-embed-server:
	@echo "Waiting for embed server to be ready..."
	@until curl -sf http://localhost:8002/health | grep -q '"model_loaded":true'; do sleep 2; done
	@echo "Embed server ready."

# ── ML scripts ────────────────────────────────────────────────────────────────

.PHONY: pretrain train

pretrain:
	$(RUN) scripts/pretrain_item_tower.py $(ARGS)

train:
	$(RUN) scripts/train_two_towers.py $(ARGS)

# ── Misc ──────────────────────────────────────────────────────────────────────

# ── Database ──────────────────────────────────────────────────────────────────

.PHONY: migrate

migrate:
	$(RUN) scripts/db_migrate.py

# ── Misc ──────────────────────────────────────────────────────────────────────

.PHONY: test format

test:
	PYTHONPATH=. pytest tests/ -v

format:
	black .
