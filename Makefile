.PHONY: cluster cilium build load deploy verify clean port-forward port-forward-api all

CLUSTER_NAME   := yellowpad
REGISTRY       := yellowpad
CILIUM_VERSION := 1.16.5

# ── Cluster setup ─────────────────────────────────────────────────────────────

cluster:
	k3d cluster create --config k3d-config.yaml
	@echo "Waiting for cluster to be ready..."
	kubectl wait --for=condition=Ready node --all --timeout=120s

cilium:
	helm repo add cilium https://helm.cilium.io/ --force-update
	helm upgrade --install cilium cilium/cilium \
	  --version $(CILIUM_VERSION) \
	  --namespace kube-system \
	  --set ingressController.enabled=true \
	  --set ingressController.default=true \
	  --set ingressController.loadbalancerMode=shared \
	  --set loadBalancer.l2.enabled=true \
	  --set loadBalancer.l2.interfaces[0]=eth0
	kubectl -n kube-system rollout status daemonset/cilium --timeout=120s

setup: cluster cilium  ## Create cluster and install Cilium CNI

# ── Build images ──────────────────────────────────────────────────────────────

build:
	docker build -t $(REGISTRY)/api-gateway:latest        ./src/api-gateway
	docker build -t $(REGISTRY)/document-processor:latest ./src/document-processor
	docker build -t $(REGISTRY)/web-ui:latest              ./src/web-ui

load:  ## Import images into k3d cluster (no registry needed)
	k3d image import $(REGISTRY)/api-gateway:latest        --cluster $(CLUSTER_NAME)
	k3d image import $(REGISTRY)/document-processor:latest --cluster $(CLUSTER_NAME)
	k3d image import $(REGISTRY)/web-ui:latest             --cluster $(CLUSTER_NAME)

# ── Deploy ────────────────────────────────────────────────────────────────────

deploy:
	kubectl apply -f k8s/
	@echo "Waiting for pods to be ready..."
	kubectl -n yellowpad wait --for=condition=Ready pod --all --timeout=180s

# ── Verify ────────────────────────────────────────────────────────────────────

verify:
	@echo "=== Pod status ==="
	kubectl get pods -n yellowpad
	@echo ""
	@echo "=== API health check ==="
	kubectl port-forward -n yellowpad svc/api-gateway 8000:8000 &
	sleep 2
	curl -s http://localhost:8000/healthz | python3 -m json.tool
	@pkill -f "port-forward.*8000" || true

# ── Port-forward shortcuts ────────────────────────────────────────────────────

port-forward:  ## Forward web-ui to http://localhost:3000
	kubectl port-forward -n yellowpad svc/web-ui 3000:80

port-forward-api:  ## Forward api-gateway to http://localhost:8000
	kubectl port-forward -n yellowpad svc/api-gateway 8000:8000

# ── Teardown ──────────────────────────────────────────────────────────────────

clean:
	k3d cluster delete $(CLUSTER_NAME)

# ── All-in-one ────────────────────────────────────────────────────────────────

all: setup build load deploy  ## Full setup: cluster + Cilium + build + deploy
