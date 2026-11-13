#!/usr/bin/env bash
# Build and verify one image: base/build.sh <gdal-minor> <python>, e.g. base/build.sh 3.12 3.13
# Tags geo-gdal:gdal<minor>-py<python>, plus geo-gdal:py<python> for the default GDAL.
set -euo pipefail
cd "$(dirname "$0")"
minor=$1 py=$2
full=$(jq -r --arg m "$minor" '.gdal[$m] // empty' matrix.json)
[ -n "$full" ] || { echo "unknown GDAL minor '$minor' (see base/matrix.json)" >&2; exit 2; }
tag="geo-gdal:gdal${minor}-py${py}"
docker build -q --build-arg "PY=$py" --build-arg "GDAL_VERSION=$full" -t "$tag" . >/dev/null
out=$(docker run --rm "$tag")
echo "$tag: $out"
case "$out" in *" gdal $full OK") ;; *) echo "expected GDAL $full" >&2; exit 1 ;; esac
if [ "$minor" = "$(jq -r .default_gdal matrix.json)" ]; then docker tag "$tag" "geo-gdal:py${py}"; fi
