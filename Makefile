.PHONY: register-builtin publish-model up-slim e2e-slim export-rfdetr quantise help base base-all lint test check up down e2e bench-large up-gpu bench-device bench-coco coco-data spacenet-data train-rfdetr train-yolox bench-spacenet licences hooks
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

up-slim:  ## like `up`, with the torch-free ONNX detector (about a quarter of the image size)
	docker image inspect geo-gdal:py3.12 >/dev/null 2>&1 || \
	  base/build.sh $$(jq -r .default_gdal base/matrix.json) 3.12
	docker compose -f compose.yaml -f compose.slim.yaml up -d --build

down:  ## stop the stack and delete its data
	docker compose down -v

e2e: down up  ## end-to-end tests on a fresh stack; artefacts in data/out/
	docker compose run --rm --build tools pytest -q -m "not slow" tests/stack

e2e-slim: down up-slim  ## every model finds both cats on the torch-free detector
	docker compose -f compose.yaml -f compose.slim.yaml run --rm --build tools pytest -q -k each_model tests/stack

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

train-rfdetr:  ## fine-tune RF-DETR (VARIANT=Small|Nano) on the SpaceNet files (GPU, ~20 min) -> data/models/rfdetr-spacenet[-nano]
	docker compose -f compose.yaml -f compose.gpu.yaml build detector
	docker build -q -t geo-train -f train/Dockerfile .
	docker run --rm --gpus all --shm-size=8g -v "$$PWD/data:/data" -v "$$PWD/scripts:/scripts:ro" \
	  --entrypoint python geo-train /scripts/train_rfdetr.py --epochs 30 --variant $(or $(VARIANT),Small)

YOLOX_NAME = yolox-spacenet$(if $(filter-out s-640,$(or $(VARIANT),s)-$(or $(SIZE),640)),-$(or $(VARIANT),s)-$(or $(SIZE),640))
train-yolox:  ## fine-tune YOLOX (VARIANT=s|tiny, SIZE=640|416) on SpaceNet (GPU), export ONNX -> data/models/yolox-spacenet[-variant-size]
	docker build -q -t geo-train -f train/Dockerfile .
	docker run --rm --gpus all --shm-size=8g -v "$$PWD/data:/data" -v "$$PWD/scripts:/scripts:ro" \
	  -e YOLOX_VARIANT=$(or $(VARIANT),s) -e YOLOX_SIZE=$(or $(SIZE),640) \
	  --entrypoint sh geo-train -c 'cd /scripts && python /opt/yolox/tools/train.py -f yolox_exp.py \
	  -d 1 -b 16 --fp16 -c /models/yolox_$(or $(VARIANT),s).pth && python export_yolox_onnx.py yolox_exp.py \
	  /data/models/$(YOLOX_NAME)/best_ckpt.pth /data/models/$(YOLOX_NAME)/yolox_buildings.onnx'

export-rfdetr:  ## export a fine-tuned RF-DETR for the slim image (RUN=rfdetr-spacenet VARIANT=Small|Nano)
	docker build -q -t geo-train -f train/Dockerfile .
	docker run --rm -v "$$PWD/data:/data" -v "$$PWD/scripts:/scripts:ro" --entrypoint python geo-train \
	  /scripts/export_rfdetr_onnx.py --variant $(or $(VARIANT),Small) --out /data/models/$(RUN) \
	  --weights /data/models/$(RUN)/checkpoint_best_total.pth

publish-model:  ## stage YOLOX-tiny and its model card for Hugging Face (REPO=user/name; UPLOAD=1 pushes, needs HF_TOKEN)
	uv run --python $(PY) --with huggingface_hub python scripts/publish_model.py --repo $(REPO) $(if $(UPLOAD),--upload)

quantise:  ## INT8-quantise a fine-tuned YOLOX (RUN=yolox-spacenet-tiny-416) -> data/models/$(RUN)/yolox_buildings_int8.onnx
	docker build -q -t geo-train -f train/Dockerfile .
	docker run --rm -v "$$PWD/data:/data" -v "$$PWD/scripts:/scripts:ro" -v "$$PWD/stages/detector:/app:ro" \
	  --entrypoint python geo-train /scripts/quantise_onnx.py /data/models/$(RUN)/yolox_buildings.onnx \
	  /data/models/$(RUN)/yolox_buildings_int8.onnx

SPACENET_MODELS ?= rfdetr-buildings,yolox-buildings,rfdetr-nano-buildings,yolox-tiny-buildings,yolox-buildings-int8,yolox-tiny-buildings-int8
bench-spacenet: down  ## fine-tuned models on the 30 held-out SpaceNet tiles (THREADS=4, OUT=bench-spacenet); report in data/out/, runs and models registered for the studio
	DETECTOR_MODELS=stub,$(SPACENET_MODELS) DETECTOR_THREADS=$(or $(THREADS),4) $(MAKE) up
	docker compose run --rm --build tools geopipe register-builtin /data/spacenet/coco
	docker compose run --rm tools geopipe bench --annotations /data/spacenet/coco/test.json \
	  --images /data/spacenet/AOI_5_Khartoum/PS-RGB --models $(SPACENET_MODELS) --limit 30 \
	  --dataset-id spacenet-khartoum --split test \
	  --out /data/out/$(or $(OUT),bench-spacenet) --title "SpaceNet Khartoum: 30 held-out tiles, $(or $(THREADS),4) CPU threads" \
	  --caveat "One city (Khartoum); the models were fine-tuned on 240 tiles of the same city, so this does not show generalisation to other places."

spacenet-data:  ## fetch 300 SpaceNet tiles (~730 MB, CC BY-SA 4.0) and write the COCO files
	uv run --python $(PY) --with boto3 python scripts/fetch_spacenet.py
	docker compose run --rm --build tools geopipe spacenet-prepare /data/spacenet/AOI_5_Khartoum /data/spacenet/coco

register-builtin:  ## make the SpaceNet dataset browsable in the studio's Explore tab (stack must be up)
	docker compose run --rm --build tools geopipe register-builtin /data/spacenet/coco

licences:  ## fail on AGPL/GPL Python packages in every image (the slim detector too, if built)
	@for img in $$(docker compose config --images | grep -v -e valkey -e seaweedfs) \
	  $$(docker image ls -q geo-pipeline-detector-slim | head -1 | sed 's/.*/geo-pipeline-detector-slim/'); do \
	  docker run --rm -v "$$PWD/scripts:/s:ro" --entrypoint python $$img /s/licence_check.py \
	    || exit 1; done

hooks:  ## install the pre-push hook
	git config core.hooksPath scripts/hooks
