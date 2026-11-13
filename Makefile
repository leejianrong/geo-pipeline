.PHONY: help base lint test check up down e2e licences hooks
.DEFAULT_GOAL := help
PY ?= 3.12
UV_RUN = uv run --python $(PY) --with ./libs/geopipe --with pytest --with ruff

help:  ## list targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

base:  ## build and verify the GDAL base images (3.12, 3.13)
	@for v in 3.12 3.13; do \
	  docker build -q --build-arg PY=$$v -t geo-gdal:py$$v base/ >/dev/null && \
	  docker run --rm geo-gdal:py$$v || exit 1; done

lint:  ## ruff
	$(UV_RUN) ruff check .

test:  ## fast tests (no Docker)
	$(UV_RUN) pytest -q tests/unit

check: lint test  ## what the pre-push hook runs

up:  ## start valkey, seaweedfs and the three stages
	docker image inspect geo-gdal:py3.12 >/dev/null 2>&1 || $(MAKE) base
	docker compose up -d --build

down:  ## stop the stack and delete its data
	docker compose down -v

e2e: up  ## end-to-end tests against the stack; artefacts in data/out/
	docker compose run --rm --build tools pytest -q tests/stack

licences:  ## fail on AGPL/GPL Python packages in every image
	@for img in $$(docker compose config --images | grep -v -e valkey -e seaweedfs); do \
	  docker run --rm -v "$$PWD/scripts:/s:ro" --entrypoint python $$img /s/licence_check.py \
	    || exit 1; done

hooks:  ## install the pre-push hook
	git config core.hooksPath scripts/hooks
