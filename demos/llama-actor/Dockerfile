# syntax=docker/dockerfile:1

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

# Qwen2.5-0.5B-Instruct served via llama.cpp + a stateful Python chat wrapper.
# Image size ~540 MB (vs ~4 GB for Ollama with the same model baked in);
# built on Debian slim with the stock llama.cpp release binary.
#
# Build:
#   docker buildx build --platform linux/amd64 \
#     -t <registry>/llama-actor:v1 --push demos/llama-actor
#
# Runtime pattern notes:
# - llama-server is started with --no-mmap. Default mmap mode leaves the
#   model file-backed, and the pages aren't captured by substrate's gVisor
#   memory snapshot. On resume the weights have to be re-paged from disk
#   through gVisor's gofer filesystem, which took minutes. --no-mmap keeps
#   weights in process anon pages, which the snapshot captures correctly.
# - chat-server.py uses ThreadingHTTPServer (not plain HTTPServer) and sets
#   Content-Length + Connection: close on every response. Without this
#   agentgateway buffers responses waiting for connection close and the
#   second request on a reused connection hangs.

FROM debian:12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 curl ca-certificates unzip libgomp1 libcurl4 && \
    rm -rf /var/lib/apt/lists/*

# llama.cpp CPU-only server. Ubuntu glibc build; libgomp1 is the only extra
# runtime dep on debian:12-slim.
ARG LLAMA_RELEASE=b6688
RUN curl -L -o /tmp/llama.zip \
      https://github.com/ggml-org/llama.cpp/releases/download/${LLAMA_RELEASE}/llama-${LLAMA_RELEASE}-bin-ubuntu-x64.zip && \
    unzip -o /tmp/llama.zip -d /opt/llama && \
    ln -s /opt/llama/build/bin/llama-server /usr/local/bin/llama-server && \
    rm /tmp/llama.zip

# Qwen2.5-0.5B-Instruct, Q4_K_M quantization. ~380 MB; bake into image so
# the golden snapshot captures a fully loaded actor.
RUN mkdir -p /models && \
    curl -L -o /models/qwen.gguf \
      https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf

COPY chat-server.py /chat-server.py
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 80
ENTRYPOINT ["/entrypoint.sh"]
