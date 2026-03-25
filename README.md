# YellowPad DevOps Engineering Challenge

## Overview

YellowPad is a legal document intelligence platform currently running on GCP (Cloud Run, AlloyDB, Pub/Sub, etc.). We are expanding to support **fully on-premises deployments** for enterprise clients with data sovereignty and compliance requirements.

This challenge asks you to take a small representative slice of our platform — three microservices and their backing infrastructure — and deploy it to a **local Kubernetes cluster** with production-readiness in mind. You'll also write a short client-facing deployment guide.

**Time expectation:** under 60 minutes.

---

## What's in This Repo

```
.
├── README.md                          # You are here
├── docker-compose.yml                 # Working Docker Compose reference
├── src/
│   ├── api-gateway/                   # Python/FastAPI — main API service
│   │   ├── app.py
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   ├── document-processor/            # Python/FastAPI — background worker
│   │   ├── app.py
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   └── web-ui/                        # Nginx — static frontend + reverse proxy
│       ├── index.html
│       ├── nginx.conf
│       └── Dockerfile
├── k8s/                               # YOUR WORK GOES HERE
│   └── .gitkeep
└── .gitignore
```

The `docker-compose.yml` shows how the services connect today. You can run `docker compose up --build` to verify everything works before starting the Kubernetes work.

### Services

| Service | Description | Ports | Dependencies |
|---------|-------------|-------|--------------|
| **api-gateway** | Main API. Accepts document uploads, stores metadata in PostgreSQL, files in MinIO, caches in Redis. Exposes `/healthz`. | 8000 | PostgreSQL, Redis, MinIO |
| **document-processor** | Worker service. Processes documents (fetches from MinIO, hashes content, updates PostgreSQL). Exposes `/healthz`. | 8001 | PostgreSQL, Redis, MinIO |
| **web-ui** | Static frontend served by Nginx. Proxies `/api/*` to the api-gateway. | 80 | api-gateway |

### Infrastructure

| Component | Image | Role | Cloud Equivalent |
|-----------|-------|------|------------------|
| PostgreSQL + pgvector | `pgvector/pgvector:pg16` | Document metadata, vector embeddings | AlloyDB |
| Redis | `redis:7-alpine` | Caching, session data | Memorystore |
| MinIO | `minio/minio:latest` | Object/document storage | Cloud Storage |

---

## The Challenge

### Part 1: Kubernetes Deployment (~35 min)

Stand up a local Kubernetes cluster and deploy the full stack using **Helm charts or Kustomize manifests** (your choice). Place all Kubernetes manifests/charts in the `k8s/` directory.

**Requirements:**

1. **Local cluster**: Use kind, minikube, or k3d — dealer's choice. Include the cluster creation command(s) in your deliverables so we can reproduce it.

2. **Deploy all three application services** (api-gateway, document-processor, web-ui) with:
   - Deployments with at least 1 replica each
   - Services for internal communication
   - Environment variables configured to connect to the backing infrastructure
   - Liveness and/or readiness probes using the `/healthz` endpoints

3. **Deploy the backing infrastructure** (PostgreSQL w/ pgvector, Redis, MinIO) with:
   - Persistent storage (PVCs) for PostgreSQL and MinIO
   - Services for internal connectivity
   - Appropriate resource requests (doesn't need to be production-tuned, but shouldn't be unbounded)

4. **Ingress or port-forwarding**: Provide a way to access the web-ui from the host machine (Ingress resource, NodePort, or documented `kubectl port-forward` commands).

5. **Secrets management**: Database credentials and MinIO keys should be in Kubernetes Secrets, not hardcoded in Deployment manifests.

6. **Namespace**: Deploy everything into a dedicated `yellowpad` namespace.

**What we're evaluating:**
- Correct use of Kubernetes primitives (Deployments, Services, ConfigMaps, Secrets, PVCs)
- Service discovery and networking between components
- Health checks and operational readiness
- Clean, well-organized manifest structure

### Part 2: Harden One Dockerfile (~10 min)

Choose **one** of the three Dockerfiles in `src/` and improve it for production use. Commit the improved Dockerfile alongside the original (or replace it in-place with a comment explaining what changed).

Consider any of the following (you don't need to do all of them):
- Multi-stage build
- Non-root user
- Pinned base image digest or specific version
- Minimal/distroless base image
- Layer caching optimization
- Health check instruction
- Security scanning metadata (labels)

### Part 3: Client Deployment Guide (~15 min)

Create a file called `DEPLOYMENT.md` in the repo root. This document should be written **for a client's IT team** — people who know Kubernetes but have never seen YellowPad before.

It should include:
1. **Prerequisites** — what the client needs before starting (hardware, software, access)
2. **Quick-start steps** — numbered instructions to go from zero to a running YellowPad instance
3. **Verification** — how to confirm the deployment is healthy
4. **Architecture overview** — a brief description (text or ASCII diagram) of how the services connect
5. **Common issues** — 2-3 things that might go wrong and how to fix them

---

## Deliverables

When you're done, your repo should contain:

- [ ] `k8s/` — Helm chart or Kustomize manifests that deploy the full stack
- [ ] At least one hardened Dockerfile (in `src/`)
- [ ] `DEPLOYMENT.md` — client-facing deployment guide
- [ ] Any scripts or Makefile entries needed to stand up the cluster and deploy

**Submission:** Push your work to a branch and open a pull request against `main`. Include any notes about trade-offs or things you'd do differently with more time in the PR description.

---

## Verification

A successful deployment should pass these checks:

```bash
# All pods running in the yellowpad namespace
kubectl get pods -n yellowpad
# Expected: all pods in Running state, all containers ready

# API health check returns all-green
kubectl port-forward -n yellowpad svc/api-gateway 8000:8000 &
curl http://localhost:8000/healthz
# Expected: {"api":"ok","database":"ok","redis":"ok","minio":"ok"}

# Document upload works end-to-end
curl -X POST http://localhost:8000/documents \
  -H "Content-Type: application/json" \
  -d '{"filename":"test.pdf","content":"hello world"}'
# Expected: {"id":1,"filename":"test.pdf","status":"pending"}

# Document processing works
curl -X POST http://localhost:8001/process/1
# (after port-forwarding document-processor)
# Expected: {"id":1,"status":"processed","content_hash":"..."}
```

---

## Notes

- **Don't over-engineer it.** We're not looking for a production HA setup with cert-manager, service mesh, and GitOps. We want to see clean, correct Kubernetes fundamentals and good operational instincts.
- **Comments are welcome.** If you'd make a different choice in a real deployment, leave a comment explaining why.
- **Time-box yourself.** If you're going over an hour, stop and note what you'd finish given more time. We value clear thinking over completeness.
- **Use whatever tools you normally use.** Helm, Kustomize, raw manifests, `kubectl create` — we care about the result, not the method.
