# llama-actor demo

Stateful chat actor backed by Qwen2.5-0.5B-Instruct running on llama.cpp
inside a gVisor sandbox. Demonstrates:

- A substrate actor built around a third-party model runtime (not a
  substrate-native Go binary).
- Golden snapshot of an LLM actor with model weights already resident in
  process memory — resume starts inference in ~5 ms rather than the
  several seconds a cold `mmap` + warm would take.
- Stateful conversation history preserved across suspend/resume by letting
  the Python wrapper own a module-level `MESSAGES` list that gVisor's
  memory snapshot captures.

Image size is ~540 MB (vs ~4 GB for an Ollama variant with the same model
baked in), because we strip the CUDA / ROCm runtime bundle and ship only
the CPU `llama-server` binary.

## Prerequisites

- An Agent Substrate control plane running (`hack/install-ate.sh --deploy-ate-system`).
- A container registry you can push to (ECR on AWS; GCR on GCP; whatever).
- `kubectl-ate` installed locally (`go install ./cmd/kubectl-ate`).
- `docker buildx` for building the image.

## 1. Build and push the image

The demo image is platform-neutral — it's a plain `debian:12-slim` with the
`llama-server` binary and GGUF model baked in. Push it to whatever registry
your substrate install reads from:

```bash
# kind (local registry started by hack/create-kind-cluster.sh)
REGISTRY=localhost:5001

# GKE
REGISTRY=gcr.io/${PROJECT_ID}/ate-images

# EKS
REGISTRY=<account>.dkr.ecr.<region>.amazonaws.com/substrate

# Then (any registry — the atelet pulls using whatever credential provider
# the node is configured with: node identity on GKE/kind, IRSA on EKS):
docker buildx build --platform linux/amd64 \
  -t "$REGISTRY/llama-actor:v1" --push demos/llama-actor

# Substrate requires actor images pinned by digest. Pull the digest from
# the registry after the push:
export LLAMA_ACTOR_IMAGE="$REGISTRY/llama-actor@$(docker buildx imagetools inspect \
  "$REGISTRY/llama-actor:v1" --format '{{json .Manifest}}' | jq -r .digest)"
```

The build pulls Qwen2.5-0.5B-Instruct Q4_K_M (~380 MB) from HuggingFace and
a pinned `llama.cpp` release binary from GitHub; nothing is pulled at
runtime.

> On EKS, make sure the atelet IRSA role carries
> `AmazonEC2ContainerRegistryReadOnly` — the kubelet image-credential-provider
> runs as atelet's subprocess, so actor image pulls go through atelet's role.
> `tools/setup-aws/` configures this automatically.

## 2. Deploy the WorkerPool and ActorTemplate

```bash
# Namespace + WorkerPool
kubectl apply -f demos/llama-actor/llama-actor.yaml

# ActorTemplate — created through the ate API. The template manifest is
# the protojson form; the atespace must already exist.
kubectl ate create atespace ate-demo-llama-actor
envsubst '${LLAMA_ACTOR_IMAGE} ${BUCKET_NAME}' \
  < demos/llama-actor/llama-actor-template.yaml.tmpl \
  | kubectl ate create actor-template -f -

# Wait for substrate to spawn the golden actor and take its snapshot (~45s
# for a 540 MB image on a new node; much faster once the image is cached).
kubectl ate get actor-templates -a ate-demo-llama-actor --watch
```

The GOLDEN TAG column going non-empty means the template is ready.

## 3. Create an actor and chat with it

```bash
kubectl ate create actor chat-1 -a ate-demo-llama-actor --template llama-actor

# Port-forward the atenet-router pod directly (the service route-forward
# load-balances across multiple router pods and can be flaky for demo
# purposes).
POD=$(kubectl get pods -n ate-system -l app=atenet-router \
       -o jsonpath='{.items[0].metadata.name}')
kubectl port-forward -n ate-system "pod/$POD" 8001:8080 &

# First request wakes the actor from SUSPENDED and takes ~4 s (snapshot
# download + gVisor restore + first inference). Subsequent requests on a
# RUNNING actor take ~1 s for short responses.
curl -X POST \
  -H "ate-target-actor: ate-demo-llama-actor/chat-1" \
  http://localhost:8001/chat \
  -d '{"message": "My name is Ant and my cat is called Nugget."}'

curl -X POST \
  -H "ate-target-actor: ate-demo-llama-actor/chat-1" \
  http://localhost:8001/chat \
  -d '{"message": "What is my cat called?"}'
```

The actor responds with `{"reply":"...","turns":N,"tokens":N}`. Conversation
history lives in a Python global, so it survives suspend/resume:

```bash
kubectl ate suspend actor chat-1 -a ate-demo-llama-actor    # snapshot to object storage
kubectl ate get actor chat-1 -a ate-demo-llama-actor        # STATE: SUSPENDED

# Next POST resumes the actor; may land on a different worker. State is
# recovered from the latest snapshot.
curl -X POST \
  -H "ate-target-actor: ate-demo-llama-actor/chat-1" \
  http://localhost:8001/chat \
  -d '{"message": "What is my cat called?"}'
# -> {"reply":"Your cat is called Nugget.","turns":N,"tokens":N}
```

Three other endpoints on the same actor:

- `GET /history` — dumps the full `MESSAGES` list (system + user + assistant turns).
- `GET /readyz` — readiness, for substrate's wakeup probe.
- `POST /reset` — drops user+assistant turns, keeps the system prompt.

## Files

| File | Purpose |
|---|---|
| `Dockerfile` | Debian-slim + pinned `llama-server` + Qwen2.5-0.5B GGUF + python3 + the chat wrapper |
| `entrypoint.sh` | Starts `llama-server` on 11434, warms the model into RAM, then starts the Python chat server on :80. The :80 listener only comes up after warm-up, so substrate's wakeup probe catches a model-loaded actor. |
| `chat-server.py` | Stateful chat wrapper. `MESSAGES` is a module-level global so it survives suspend/resume via gVisor memory snapshot. Uses `ThreadingHTTPServer` and sets `Content-Length` on every response — see "gotchas" below. |
| `llama-actor.yaml` | Namespace + `WorkerPool` (2 replicas, 2 CPU / 3 GiB per worker) |
| `llama-actor-template.yaml.tmpl` | ActorTemplate; expects `${LLAMA_ACTOR_IMAGE}` (digest-pinned) and `${BUCKET_NAME}` substituted before `kubectl ate create` |

## Gotchas worth knowing before you adapt this

- **`--no-mmap` is load-bearing.** Default-`mmap` llama.cpp keeps weights
  file-backed. gVisor's memory snapshot captures anon pages, not
  file-backed pages — resume comes back cold and the first inference
  blocks for ~90 s re-paging weights through the gofer FS. `--no-mmap`
  reads weights into process heap, which survives the snapshot cleanly.
- **`Content-Length` on every response + `ThreadingHTTPServer`.** Python's
  default `http.server.HTTPServer` is single-threaded and
  `BaseHTTPRequestHandler` doesn't set `Content-Length`. When
  agentgateway (atenet-router) proxies the actor's response, it waits for
  the backend to close the connection to know the body is complete — and
  because the server loops to accept more requests on the same keep-alive
  connection, the second curl hangs until something forcibly closes
  (which happens when you suspend). Setting `Content-Length` + using
  `ThreadingHTTPServer` + sending `Connection: close` closes the hole.
- **No GPU.** gVisor's standard runtime doesn't do GPU passthrough, so
  inference is CPU-only regardless of platform. That's why "tiny" (≤0.5B
  parameters) matters. Qwen2.5-0.5B runs at ~20-40 tok/s on 2 vCPUs — fine
  for a demo, not a serving tier.
- **Image size scales with model.** Switching to Qwen2.5-1.5B (~1 GB
  quantized) is a one-line change in the Dockerfile but inflates the
  image to ~1.3 GB; the first-time pull on a new node adds proportionally
  to the actor's cold-start latency.

## What makes this a substrate demo rather than a Kubernetes demo

Any Kubernetes operator can run llama.cpp in a pod. What substrate adds:
you can create 100 of these actors from this one template, keep 99 of them
suspended at zero compute cost, and resume any specific one on an HTTP
request in ~4 seconds **with its full conversation history and the model
already in RAM**. Normal Kubernetes needs 100 running pods or accepts
cold-starting llama.cpp on every resume (several seconds for model load +
loss of conversation state).
