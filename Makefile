.ONESHELL:
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := all

VENV_BIN := environment/bin
VENV_PYTHON := $(VENV_BIN)/python3
VENV_PIP := $(VENV_BIN)/pip3
PYTHON := $(shell if [ -x "$(VENV_PYTHON)" ]; then echo "$(VENV_PYTHON)"; else echo python3; fi)
PIP := $(shell if [ -x "$(VENV_PIP)" ]; then echo "$(VENV_PIP)"; else echo pip3; fi)
ALEMBIC := $(VENV_BIN)/alembic
PYTEST := $(VENV_BIN)/pytest
PYTHON_VERSION ?= python3.12
SYNTHESIS_PYTHON ?= python3.11
LEARNING_AUDIO_VOICE ?= female
COSYVOICE_ASR_DIR ?= /path/to/audio-tools/asr
LEARNING_AUDIO_VOCABULARY ?= ../heyhanzi-ui-web/public/curriculum-data/mandarin-hsk2025.json
RELEASE_IMAGE_TAG ?= heyhanzi-backend-release-check:local
RELEASE_REPORTS_DIR ?= .deployment-reports
SECURITY_PYTHON ?= .security-environment/bin/python3
export LOCAL_UID := $(shell id -u)
export LOCAL_GID := $(shell id -g)

all: help
help:
	@echo "Development: make dev-up | dev-down | dev-logs | db-up | db-down | db-logs"
	echo "Setup: make env | dev-requirements | certificates | add-hosts | local-https"
	echo 'Database: make migrate | migration MSG="description" | ensure-db-exists'
	echo "Checks: make test | ci-core | verify-local-full | release-check"
	echo "Deploy: manual GitHub Actions workflow (see README.md)"

.env:
	@umask 077
	cp .env.example .env

env: local-auth
	$(PYTHON_VERSION) -m venv environment
	$(VENV_PYTHON) -m pip install --upgrade pip
	$(VENV_PIP) install -r requirements.txt

dev-requirements:
	$(PIP) install -r requirements-dev.txt

requirements:
	$(VENV_BIN)/pip-compile --strip-extras -o requirements.txt requirements.in
	$(VENV_BIN)/pip-compile --strip-extras -o requirements-dev.txt requirements-dev.in
	$(VENV_BIN)/pip-compile --strip-extras -o requirements-security.txt requirements-security.in

ensure_mkcert:
	@command -v mkcert >/dev/null || { echo "Install mkcert, then run mkcert -install." >&2; exit 1; }

certificates: ensure_mkcert
	@mkdir -p .cert
	if [ ! -f .cert/cert.pem ] || [ ! -f .cert/key.pem ]; then
		mkcert -key-file=.cert/key.pem -cert-file=.cert/cert.pem local-api.heyhanzi.com localhost 127.0.0.1 ::1
	fi
	cp "$$(mkcert -CAROOT)/rootCA.pem" .cert/rootCA.pem
	chmod 600 .cert/key.pem
	chmod 644 .cert/cert.pem .cert/rootCA.pem

add-hosts:
	@if ! getent hosts local-api.heyhanzi.com | grep -q '^127\.0\.0\.1'; then
		echo '127.0.0.1 local-api.heyhanzi.com' | sudo tee -a /etc/hosts
	fi

local-https: certificates add-hosts

db-up: .env
	docker compose up -d --wait postgres

db-down:
	docker compose stop postgres

db-logs:
	docker compose logs -f postgres

local-auth: .env
	$(PYTHON) scripts/setup_local_auth.py

dev-up: .env certificates local-auth
	docker compose up --build $(ARGS)

dev-down:
	docker compose down

dev-logs:
	docker compose logs -f backend database-bootstrap

test: db-up dev-requirements behavior-only-policy
	$(PYTEST) $(ARGS)

test-parallel: test
	@echo "The initial suite is small; this Compatible alias runs the full suite."

lint:
	$(VENV_BIN)/ruff check app scripts tests run.py alembic

behavior-only-policy:
	$(PYTHON) scripts/check_behavior_only_tests.py
	$(PYTHON) scripts/check_application_boundary_policy.py

ci-core: lint behavior-only-policy
	$(PYTEST) -m 'not integration'

verify-local-full: db-up
	$(MAKE) ci-core
	$(MAKE) release-schema

release-preflight: ci-core
	$(PYTHON) -m compileall -q app scripts

release-schema:
	$(PYTEST) -m integration

release-container:
	docker build --build-arg DEPENDENCY_AUDIT_NONCE="$$(date +%s)" -t "$(RELEASE_IMAGE_TAG)" .
	$(PYTHON) scripts/benchmark_release_image_memory.py --image "$(RELEASE_IMAGE_TAG)"

security-env:
	$(PYTHON_VERSION) -m venv .security-environment
	.security-environment/bin/pip install -r requirements-security.txt

audit-runtime-security:
	mkdir -p "$(RELEASE_REPORTS_DIR)"
	$(SECURITY_PYTHON) -m pip_audit --disable-pip --no-deps -r requirements.txt -f json -o "$(RELEASE_REPORTS_DIR)/python-dependency-audit.json"

release-target-static-check:
	HEYHANZI_DISABLE_DOTENV=true APP_ENV=PROD $(PYTHON) scripts/validate_runtime_config.py

release-target-check: release-target-static-check

release-check: db-up dev-requirements
	$(MAKE) release-preflight
	$(MAKE) release-schema
	$(MAKE) release-container

create-db ensure-db-exists:
	$(PYTHON) scripts/ensure_db_exists.py

migrate: create-db
	$(PYTHON) scripts/bootstrap_database.py

# Support both make migration "message" and MSG="message".
ifeq ($(firstword $(MAKECMDGOALS)),migration)
MIGRATION_MESSAGE := $(if $(MSG),$(MSG),$(wordlist 2,99,$(MAKECMDGOALS)))
# Quoted messages arrive as a single target containing spaces; consume only in
# migration mode so misspelled ordinary commands still fail loudly.
%:
	@:
endif
export MIGRATION_MESSAGE
migration:
	@test -n "$$MIGRATION_MESSAGE" || { echo 'Usage: make migration MSG="description"' >&2; exit 2; }
	$(ALEMBIC) revision --autogenerate -m "$$MIGRATION_MESSAGE"

# One-off content preparation; never started with the API or deployment.
learning-audio-catalogue:
	$(PYTHON) -m scripts.curriculum.build_audio_catalogue --vocabulary "$(LEARNING_AUDIO_VOCABULARY)"

learning-audio-configure:
	$(PYTHON) -m scripts.configure_learning_audio $(ARGS)

learning-audio-cdn:
	$(PYTHON) -m scripts.configure_audio_cdn

learning-audio-generate:
	@test -n "$(COSYVOICE_SOURCE)" -a -n "$(COSYVOICE_MODEL_DIR)" -a -n "$(COSYVOICE_REFERENCE)" || { echo "Set COSYVOICE_SOURCE, COSYVOICE_MODEL_DIR and COSYVOICE_REFERENCE." >&2; exit 2; }
	PYTHONPATH="$(CURDIR)" $(SYNTHESIS_PYTHON) scripts/generate_sentence_audio.py --cosyvoice-source "$(COSYVOICE_SOURCE)" --model-dir "$(COSYVOICE_MODEL_DIR)" --reference "$(COSYVOICE_REFERENCE)" --asr "$(COSYVOICE_ASR_DIR)" --voice "$(LEARNING_AUDIO_VOICE)" $(ARGS)

learning-audio-audit:
	PYTHONPATH="$(CURDIR)" $(SYNTHESIS_PYTHON) -m scripts.audio_pipeline.publish --output .audio-production/audited $(ARGS)

learning-audio-publish:
	PYTHONPATH="$(CURDIR)" $(SYNTHESIS_PYTHON) -m scripts.audio_pipeline.publish --output .audio-production/releases --activate $(ARGS)

resetdb:
	$(PYTHON) scripts/reset_db.py --confirm "$(CONFIRM)"

.PHONY: all help env dev-requirements requirements ensure_mkcert certificates add-hosts local-https local-auth db-up db-down db-logs dev-up dev-down dev-logs test test-parallel lint behavior-only-policy ci-core verify-local-full release-preflight release-schema release-container security-env audit-runtime-security release-target-static-check release-target-check release-check create-db ensure-db-exists migrate migration resetdb
.PHONY: learning-audio-catalogue learning-audio-configure learning-audio-cdn learning-audio-generate learning-audio-audit learning-audio-publish

# Offline production only. All model dependencies stay in SYNTHESIS_PYTHON.
learning-audio-prepare-v2:
	PYTHONPATH="$(CURDIR)" $(SYNTHESIS_PYTHON) -m scripts.audio_pipeline.prepare $(ARGS)

learning-audio-pilot:
	PYTHONPATH="$(CURDIR)" $(SYNTHESIS_PYTHON) -m scripts.audio_pipeline.pilot $(ARGS)

learning-audio-produce:
	PYTHONPATH="$(CURDIR)" $(SYNTHESIS_PYTHON) -m scripts.audio_pipeline.run $(ARGS)

learning-audio-release:
	PYTHONPATH="$(CURDIR)" $(SYNTHESIS_PYTHON) -m scripts.audio_pipeline.publish $(ARGS)

learning-audio-cleanup:
	PYTHONPATH="$(CURDIR)" $(SYNTHESIS_PYTHON) -m scripts.audio_pipeline.cleanup $(ARGS)

.PHONY: learning-audio-prepare-v2 learning-audio-pilot learning-audio-produce learning-audio-release learning-audio-cleanup

learning-audio-status:
	PYTHONPATH="$(CURDIR)" $(SYNTHESIS_PYTHON) -m scripts.audio_pipeline.status

# Offline CLI regressions use the synthesis dependencies, never the API image.
learning-audio-test:
	PYTHONPATH="$(CURDIR)" $(SYNTHESIS_PYTHON) -m unittest discover -s scripts/audio_pipeline/tests -v

.PHONY: learning-audio-status learning-audio-test

stripe-configure:
	$(PYTHON) -m scripts.stripe_local --configure

stripe-listen:
	$(PYTHON) -m scripts.stripe_local

.PHONY: stripe-configure stripe-listen
