#!/bin/sh

# Copyright 2026 Ant Weiss
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

set -e

# --no-mmap: force llama.cpp to read weights into process anon pages so
# substrate's gVisor memory snapshot captures them. Default mmap mode leaves
# weights file-backed and cold on resume, which took ~minutes to re-page.
llama-server \
  --model /models/qwen.gguf \
  --host 127.0.0.1 --port 11434 \
  -c 2048 -t 2 \
  --jinja \
  --no-mmap \
  > /tmp/llama.log 2>&1 &
LS_PID=$!

for i in $(seq 1 180); do
  curl -sf http://127.0.0.1:11434/health >/dev/null 2>&1 && break
  sleep 0.5
done

# Warm the model — this now also pre-faults all weight pages because of --no-mmap
curl -sf -X POST http://127.0.0.1:11434/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen","messages":[{"role":"user","content":"ok"}],"max_tokens":1}' \
  >/dev/null && echo "warm-up ok" || echo "warm-up failed"

echo "chat server on :80"
python3 /chat-server.py &
PY_PID=$!

wait $LS_PID
kill $PY_PID 2>/dev/null
