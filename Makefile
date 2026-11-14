.PHONY: help base base-all lint test check up down e2e bench-large up-gpu bench-device bench-coco coco-data spacenet-data train-rfdetr train-yolox bench-spacenet licences hooks
.DEFAULT_GOAL := help
PY ?= 3.12
UV_RUN = uv run --python $(PY) --with-editable ./libs/geopipe --with matplotlib --with pycocotools --with pytest --with ruff --with fastapi --with httpx

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

up-gpu:  ## like `up`, with the detector on the GPU (NVIDIA runtime required)
	docker image inspect geo-gdal:py3.12 >/dev/null 2>&1 || \
	  base/build.sh $$(jq -r .default_gdal base/matrix.json) 3.12
	docker compose -f compose.yaml -f compose.gpu.yaml up -d --build

down:  ## stop the stack and delete its data
	docker compose down -v

e2e: down up  ## end-to-end tests on a fresh stack; artefacts in data/out/
	docker compose run --rm --build tools pytest -q -m "not slow" tests/stack

bench-large: down up  ## 2 GB raster: slicer memory and COG vs non-COG timing (slow)
	docker compose run --rm --build tools pytest -q -s -m slow tests/stack

bench-device: down up  ## tiles/s and parity, CPU then GPU; report in data/out/device-*.json
	DEVICE_TAG=cpu docker compose run --rm --build -e DEVICE_TAG tools pytest -q -s -m slow tests/stack/test_device.py
	$(MAKE) down up-gpu
	DEVICE_TAG=gpu docker compose -f compose.yaml -f compose.gpu.yaml run --rm --build -e DEVICE_TAG tools pytest -q -s -m slow tests/stack/test_device.py

bench-coco: coco-data up  ## RF-DETR vs YOLOX on 200 COCO val images; report in data/out/bench/
	docker compose run --rm --build tools geopipe bench --annotations /data/coco/instances_val2017.json \
	  --images /data/coco/images --download --limit 200 --out /data/out/bench

coco-data:  ## fetch COCO val2017 annotations (241 MB download) into data/coco/
	@mkdir -p data/coco
	@test -f data/coco/instances_val2017.json || { \
	  curl -fL -o data/coco/annotations.zip http://images.cocodataset.org/annotations/annotations_trainval2017.zip && \
	  python3 -c "import os, zipfile; z = zipfile.ZipFile('data/coco/annotations.zip'); z.extract('annotations/instances_val2017.json', 'data/coco'); os.replace('data/coco/annotations/instances_val2017.json', 'data/coco/instances_val2017.json'); os.rmdir('data/coco/annotations'); z.close(); os.remove('data/coco/annotations.zip')"; }
	@ls -lh data/coco/instances_val2017.json

train-rfdetr:  ## fine-tune RF-DETR Small on the SpaceNet files (GPU, ~20 min) -> data/models/rfdetr-spacenet
	docker compose -f compose.yaml -f compose.gpu.yaml build detector
	docker build -q -t geo-train -f train/Dockerfile .
	docker run --rm --gpus all --shm-size=8g -v "$$PWD/data:/data" -v "$$PWD/scripts:/scripts:ro" \
	  --entrypoint python geo-train /scripts/train_rfdetr.py --epochs 30

train-yolox:  ## fine-tune YOLOX-s on the SpaceNet files (GPU) and export ONNX -> data/models/yolox-spacenet
	docker build -q -t geo-train -f train/Dockerfile .
	docker run --rm --gpus all --shm-size=8g -v "$$PWD/data:/data" -v "$$PWD/scripts:/scripts:ro" \
	  --entrypoint sh geo-train -c 'cd /scripts && python /opt/yolox/tools/train.py -f yolox_exp.py \
	  -d 1 -b 16 --fp16 -c /models/yolox_s.pth && python export_yolox_onnx.py yolox_exp.py \
	  /data/models/yolox-spacenet/best_ckpt.pth /data/models/yolox-spacenet/yolox_buildings.onnx'

bench-spacenet: down  ## fine-tuned RF-DETR and YOLOX on the 30 held-out SpaceNet tiles; report in data/out/bench-spacenet/
	DETECTOR_MODELS=stub,rfdetr-buildings,yolox-buildings $(MAKE) up
	docker compose run --rm --build tools geopipe bench --annotations /data/spacenet/coco/test.json \
	  --images /data/spacenet/AOI_5_Khartoum/PS-RGB --models rfdetr-buildings,yolox-buildings --limit 30 \
	  --out /data/out/bench-spacenet --title "SpaceNet Khartoum: 30 held-out tiles" \
	  --caveat "One city (Khartoum); the models were fine-tuned on 240 tiles of the same city, so this does not show generalisation to other places."

spacenet-data:  ## fetch 300 SpaceNet tiles (~730 MB, CC BY-SA 4.0) and write the COCO files
	uv run --python $(PY) --with boto3 python scripts/fetch_spacenet.py
	docker compose run --rm --build tools geopipe spacenet-prepare /data/spacenet/AOI_5_Khartoum /data/spacenet/coco

licences:  ## fail on AGPL/GPL Python packages in every image
	@for img in $$(docker compose config --images | grep -v -e valkey -e seaweedfs); do \
	  docker run --rm -v "$$PWD/scripts:/s:ro" --entrypoint python $$img /s/licence_check.py \
	    || exit 1; done

hooks:  ## install the pre-push hook
	git config core.hooksPath scripts/hooks
