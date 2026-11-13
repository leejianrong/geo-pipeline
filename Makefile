.PHONY: help base base-all lint test check up down e2e bench-large licences hooks
.DEFAULT_GOAL := help
PY ?= 3.12
UV_RUN = uv run --python $(PY) --with-editable ./libs/geopipe --with matplotlib --with pytest --with ruff

help:  ## list targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

base:  ## build and verify the default GDAL (base/matrix.json) for each Python
	@for py in $$(jq -r '.python[]' base/matrix.json); do \
	  base/build.sh $$(jq -r .default_gdal base/matrix.json) $$py || exit 1; done

base-all:  ## every GDAL x Python combination in base/matrix.json (slow)
	@for m in $$(jq -r '.gdal | keys[]' base/matrix.json); do \
	  for py in $$(jq -r '.python[]' base/matrix.json); do base/build.sh $$m $$py || exit 1; done; done

lint:  ## ruff
	$(UV_RUN) ruff check .

test:  ## fast tests (no Docker)
	$(UV_RUN) pytest -q tests/unit

check: lint test  ## what the pre-push hook runs

up:  ## start valkey, seaweedfs and the three stages
	docker image inspect geo-gdal:py3.12 >/dev/null 2>&1 || \
	  base/build.sh $$(jq -r .default_gdal base/matrix.json) 3.12
	docker compose up -d --build

down:  ## stop the stack and delete its data
	docker compose down -v

e2e: up  ## end-to-end tests against the stack; artefacts in data/out/
	docker compose run --rm --build tools pytest -q -m "not slow" tests/stack

bench-large: up  ## 2 GB raster: slicer memory and COG vs non-COG timing (slow)
	docker compose run --rm --build tools pytest -q -s -m slow tests/stack

licences:  ## fail on AGPL/GPL Python packages in every image
	@for img in $$(docker compose config --images | grep -v -e valkey -e seaweedfs); do \
	  docker run --rm -v "$$PWD/scripts:/s:ro" --entrypoint python $$img /s/licence_check.py \
	    || exit 1; done

hooks:  ## install the pre-push hook
	git config core.hooksPath scripts/hooks
