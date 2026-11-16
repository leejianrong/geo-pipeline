#!/bin/sh
# collect-libs.sh OUTDIR PATH...
# Copy every shared library that the ELF files under PATH... need into OUTDIR, keeping their
# paths (with /lib resolved to /usr/lib), so `COPY --from=build /OUTDIR/ /` puts them in place.
# The C library itself is left out: the distroless base already has it.
set -eu
out=$1; shift
find "$@" -type f \( -name '*.so' -o -name '*.so.*' -o -perm -u+x \) 2>/dev/null | while read -r f; do
  ldd "$f" 2>/dev/null | awk '/=> \//{print $3}'
done | sort -u | while read -r p; do
  case "$(basename "$p")" in
    ld-linux*|libc.so.*|libm.so.*|libdl.so.*|libpthread.so.*|librt.so.*|libutil.so.*|libresolv.so.*) continue ;;
  esac
  dir=$(realpath "$(dirname "$p")"); name=$(basename "$p")
  for src in "$dir/$name" "$(readlink -f "$dir/$name")"; do
    mkdir -p "$out$(dirname "$src")"; cp -a "$src" "$out$src"
  done
done
