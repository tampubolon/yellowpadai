# YellowPad — On-Premises Deployment Guide

This guide is written for your IT team. It walks you through deploying YellowPad on a local Kubernetes cluster from scratch.

---

## Prerequisites

### Hardware
- 1 machine (or VM) with at least **4 vCPUs** and **8 GB RAM**
- 40 GB free disk (PostgreSQL + MinIO storage)

### Software
| Tool | Version | Purpose |
|------|---------|---------|
| Docker | 24+ | Build images, run k3d nodes |
| k3d | 5.6+ | Local Kubernetes cluster (k3s in Docker) |
| kubectl | 1.29+ | Cluster management |
| Helm | 3.14+ | Install Cilium CNI |
| make | any | Convenience commands (optional) |

Install k3d:
```bash
curl -s https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | bash
```

### Access
- Internet access to pull images on first run (docker.io, quay.io)
- No cloud account required — fully self-contained

---

## Quick-Start

### 1. Create the cluster

```bash
k3d cluster create --config k3d-config.yaml
```

This creates a 2-node k3d cluster (1 server + 1 agent) with flannel, Traefik, and the built-in service LB all disabled — ready for Cilium to take over.

Verify: `kubectl get nodes` — both nodes should show `Ready` within ~30 seconds.

### 2. Install Cilium (CNI + Ingress controller)

```bash
helm repo add cilium https://helm.cilium.io/
helm install cilium cilium/cilium \
  --version 1.16.5 \
  --namespace kube-system \
  --set ingressController.enabled=true \
  --set ingressController.default=true \
  --set ingressController.loadbalancerMode=shared \
  --set loadBalancer.l2.enabled=true \
  --set loadBalancer.l2.interfaces[0]=eth0
```

> Replace `eth0` with your machine's primary network interface (`ip link` to find it).

Wait for Cilium to be ready:
```bash
kubectl -n kube-system rollout status daemonset/cilium
```

### 3. Configure the local IP pool

Edit `k8s/11-cilium-lb-ippool.yaml` and set the `cidr` to a free range on your local network:
```yaml
blocks:
  - cidr: "192.168.1.200/29"   # 6 usable IPs — adjust to your LAN
```

If unsure, ask your network admin for a small block of unused IPs on the same subnet as your machine.

### 4. Build and load images

```bash
# Build
docker build -t yellowpad/api-gateway:latest        ./src/api-gateway
docker build -t yellowpad/document-processor:latest ./src/document-processor
docker build -t yellowpad/web-ui:latest              ./src/web-ui

# Import directly into the k3d cluster (no registry needed)
k3d image import yellowpad/api-gateway:latest        --cluster yellowpad
k3d image import yellowpad/document-processor:latest --cluster yellowpad
k3d image import yellowpad/web-ui:latest             --cluster yellowpad
```

Or with make: `make build load`

### 5. Deploy

```bash
kubectl apply -f k8s/
```

Watch pods start up:
```bash
kubectl get pods -n yellowpad -w
```

All pods should reach `Running` state within 2–3 minutes. PostgreSQL takes longest (it runs an init sequence).

### 6. Access the UI

```bash
# Find the IP assigned by Cilium LB IPAM
kubectl -n kube-system get svc cilium-ingress-yellowpad-yellowpad-ingress
```

Add the IP to your `/etc/hosts`:
```
192.168.1.200  yellowpad.local
```

Open **http://yellowpad.local** in your browser.

Alternatively, use port-forward (no DNS needed):
```bash
kubectl port-forward -n yellowpad svc/web-ui 3000:80
# Then open http://localhost:3000
```

---

## Verification

Run these checks after deployment to confirm everything is healthy.

### All pods running
```bash
kubectl get pods -n yellowpad
# Expected: all pods STATUS=Running, READY=1/1
```

### API health check
```bash
kubectl port-forward -n yellowpad svc/api-gateway 8000:8000 &
curl http://localhost:8000/healthz
# Expected: {"api":"ok","database":"ok","redis":"ok","minio":"ok"}
```

### End-to-end document upload
```bash
# Upload a document
curl -X POST http://localhost:8000/documents \
  -H "Content-Type: application/json" \
  -d '{"filename":"test.pdf","content":"hello world"}'
# Expected: {"id":1,"filename":"test.pdf","status":"pending"}

# Process it
kubectl port-forward -n yellowpad svc/document-processor 8001:8001 &
curl -X POST http://localhost:8001/process/1
# Expected: {"id":1,"status":"processed","content_hash":"..."}
```

---

## Architecture Overview

```
                          ┌──────────────────────────────────────┐
                          │         yellowpad namespace           │
                          │                                       │
  Browser ──► Cilium      │  ┌─────────┐    ┌──────────────────┐ │
              Ingress ────┼─►│ web-ui  │───►│   api-gateway    │ │
              (LB IPAM)   │  │ nginx   │    │   FastAPI :8000  │ │
                          │  └─────────┘    └───┬──────┬───┬───┘ │
                          │                     │      │   │     │
                          │              ┌──────┘  ┌───┘   └──────────────┐
                          │              ▼          ▼                      ▼
                          │  ┌──────────────┐  ┌────────┐  ┌─────────────────────┐
                          │  │  PostgreSQL  │  │ Redis  │  │ document-processor  │
                          │  │  pgvector    │  │ cache  │  │ FastAPI :8001        │
                          │  │  :5432 + PVC │  │ :6379  │  └────┬──────┬─────┬───┘
                          │  └──────────────┘  └────────┘       │      │     │
                          │         ▲                ▲           │      │     │
                          │         └────────────────┴───────────┘      │     │
                          │                                              ▼     │
                          │                                    ┌─────────────┐ │
                          │                                    │    MinIO    │ │
                          │                                    │  :9000+PVC  │ │
                          │                                    └─────────────┘ │
                          └──────────────────────────────────────────────────┘
```

**Request flow:**
1. Browser → Cilium Ingress (IP from LB IPAM pool)
2. Ingress → `web-ui` (nginx serves the SPA)
3. Browser JS calls `/api/*` → nginx proxies to `api-gateway:8000`
4. api-gateway stores metadata in PostgreSQL, files in MinIO, caches in Redis
5. Document processing triggered via `document-processor:8001`

---

## Common Issues

### Pods stuck in `Pending`

**Symptom:** `kubectl get pods -n yellowpad` shows pods in `Pending` state.

**Cause:** Usually PersistentVolumeClaim not bound, or insufficient resources.

**Fix:**
```bash
kubectl describe pod <pod-name> -n yellowpad  # look at Events section
kubectl get pvc -n yellowpad                  # check PVC status
```
k3d uses the local-path provisioner by default, which provisions PVCs automatically from the host filesystem. If PVCs are stuck, confirm the cluster is healthy: `k3d cluster list`.

---

### api-gateway readiness probe failing

**Symptom:** api-gateway pod is `Running` but not `Ready` (0/1).

**Cause:** `/healthz` returns 503 when PostgreSQL, Redis, or MinIO isn't ready yet. This is expected during startup.

**Fix:** Wait 1–2 minutes for all backing services to become healthy. Check:
```bash
kubectl logs -n yellowpad deploy/api-gateway
kubectl get pods -n yellowpad  # postgres-0, redis, and minio must be Running first
```

---

### Cannot reach http://yellowpad.local

**Symptom:** Browser shows "site can't be reached".

**Cause A:** `/etc/hosts` entry missing or wrong IP.

**Fix:** Re-check the IP assigned by Cilium:
```bash
kubectl -n kube-system get svc cilium-ingress-yellowpad-yellowpad-ingress \
  -o jsonpath='{.status.loadBalancer.ingress[0].ip}'
```
Update `/etc/hosts` with the correct IP.

**Cause B:** Cilium LB IPAM pool CIDR not on your local network.

**Fix:** Edit `k8s/11-cilium-lb-ippool.yaml`, update the `cidr` to a range reachable from your machine, then `kubectl apply -f k8s/11-cilium-lb-ippool.yaml`.

**Fallback:** Skip Ingress entirely and use port-forward:
```bash
kubectl port-forward -n yellowpad svc/web-ui 3000:80
# Open http://localhost:3000
```
