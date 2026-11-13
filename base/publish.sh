#!/usr/bin/env bash
# Push an image built by build.sh: base/publish.sh <gdal-minor> <python> <registry/name>
# Moving tags: gdal<minor>-py<python> (and py<python> for the default GDAL).
# Immutable tag: py<python>-gdal<full>-numpy<numpy>. Then pulls it back and verifies it.
set -euo pipefail
cd "$(dirname "$0")"
minor=$1 py=$2 repo=$3
local_tag="geo-gdal:gdal${minor}-py${py}"
out=$(docker run --rm "$local_tag")
gdal=$(sed -n 's/.* gdal \([0-9.]*\) OK/\1/p' <<<"$out")
numpy=$(sed -n 's/.* numpy \([0-9.]*\) .*/\1/p' <<<"$out")
tags=("gdal${minor}-py${py}" "py${py}-gdal${gdal}-numpy${numpy}")
if [ "$minor" = "$(jq -r .default_gdal matrix.json)" ]; then tags+=("py${py}"); fi
for t in "${tags[@]}"; do docker tag "$local_tag" "$repo:$t"; docker push -q "$repo:$t"; done
# Verify what a user would pull, not the local build.
docker image rm -f "$local_tag" "${tags[@]/#/$repo:}" >/dev/null
docker pull -q "$repo:${tags[1]}" >/dev/null
echo "published ${tags[*]}"
docker run --rm "$repo:${tags[1]}"
