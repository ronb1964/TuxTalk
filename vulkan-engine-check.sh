#!/bin/bash
# Runs a built whisper-server and checks what TuxTalk's patches promise:
# without its key (vulkan-engine-patches/server-api-key.patch) only a plain
# health check is answered, and there is no /load route
# (server-no-load-route.patch). A published engine build can never be
# replaced, so vulkan-engine-container.sh runs this before packing one.
#
# Usage: ./vulkan-engine-check.sh <engine folder> <whisper model>
# whisper.cpp's own models/for-tests-ggml-tiny.bin is enough: the server
# starts with it and answers with empty text. Runs on the processor.
set -e
if [ $# -ne 2 ]; then
    echo "Usage: $0 <engine folder> <whisper model>" >&2
    exit 1
fi
ENGINE="$1"
MODEL="$2"
KEY=$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')
PREFIX="/$(head -c 8 /dev/urandom | od -An -tx1 | tr -d ' \n')"
PORT=$((20000 + RANDOM % 20000))
URL="http://127.0.0.1:$PORT$PREFIX"
PUBLIC=$(mktemp -d)
echo "a static file" > "$PUBLIC/static.txt"

LD_LIBRARY_PATH="$ENGINE" WHISPER_API_KEY="$KEY" "$ENGINE/whisper-server" -m "$MODEL" \
    --host 127.0.0.1 --port "$PORT" --request-path "$PREFIX" --public "$PUBLIC" \
    -ng -t 2 >/dev/null 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null; rm -rf "$PUBLIC"' EXIT
for _ in $(seq 120); do
    [ "$(curl -s -o /dev/null -w '%{http_code}' "$URL/health")" = 200 ] && break
    sleep 0.5
done

FAILED=0
# expect <HTTP status> <what is checked> <curl arguments>
expect() {
    local want="$1" what="$2" got
    shift 2
    got=$(curl -s -o /dev/null -w '%{http_code}' --max-time 60 "$@" || true)
    if [ "$got" = "$want" ]; then
        echo "  ok    $what: $got"
    else
        echo "  FAIL  $what: $got, not $want"
        FAILED=1
    fi
}
KEYED=(-H "Authorization: Bearer $KEY")
AUDIO=(-F "file=@$ENGINE/speed-check.wav" -F response_format=json)
expect 200 "health, no key" "$URL/health"
expect 401 "health posted with a body, no key" --data x "$URL/health"
expect 401 "health as a GET with a body, no key" -X GET --data x "$URL/health"
expect 401 "inference, no key" "${AUDIO[@]}" "$URL/inference"
expect 401 "inference, wrong key" -H "Authorization: Bearer x$KEY" "${AUDIO[@]}" "$URL/inference"
expect 401 "inference, key cut short" -H "Authorization: Bearer ${KEY:0:16}" "${AUDIO[@]}" \
    "$URL/inference"
expect 401 "index page, no key" "$URL/"
expect 401 "static file, no key" "http://127.0.0.1:$PORT/static.txt"
expect 401 "OPTIONS, no key" -X OPTIONS "$URL/inference"
expect 401 "load, no key" -F "model=$MODEL" "$URL/load"
expect 404 "load, with the key" "${KEYED[@]}" -F "model=$MODEL" "$URL/load"
expect 200 "static file, with the key" "${KEYED[@]}" "http://127.0.0.1:$PORT/static.txt"
expect 200 "inference, with the key" "${KEYED[@]}" "${AUDIO[@]}" "$URL/inference"
exit $FAILED
