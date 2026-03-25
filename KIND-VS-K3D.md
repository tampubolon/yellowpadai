# kind vs k3d — Local Kubernetes Cluster Comparison

Both tools run Kubernetes locally inside Docker containers. This project uses **k3d**.

---

## At a Glance

| | kind | k3d |
|---|---|---|
| Full name | Kubernetes IN Docker | k3s IN Docker |
| Kubernetes distribution | Upstream (kubeadm) | k3s (lightweight, by Rancher) |
| Startup time | ~60–90s | ~20–30s |
| Resource usage | Higher | Lower |
| Default CNI | kindnet | flannel |
| Default ingress | None | Traefik |
| Default LB | None | Klipper ServiceLB |
| Image loading | `kind load docker-image` | `k3d image import` |
| Config file API | `kind.x-k8s.io/v1alpha4` | `k3d.io/v1alpha5` |
| Cilium setup complexity | Disable one thing | Disable three things |

---

## How Cilium setup differs

### kind
Only one thing to disable — the default CNI (kindnet):

```yaml
# kind-config.yaml
networking:
  disableDefaultCNI: true
```

### k3d
Three things to disable — flannel (CNI), Traefik (ingress), and Klipper (LB):

```yaml
# k3d-config.yaml
options:
  k3s:
    extraArgs:
      - arg: --flannel-backend=none
        nodeFilters: [server:*]
      - arg: --disable=traefik
        nodeFilters: [server:*]
      - arg: --disable=servicelb
        nodeFilters: [server:*]
```

Slightly more ceremony upfront, but the result is a clean slate for Cilium to own all three concerns.

---

## Why k3d for this project

**Startup speed.** k3d clusters are ready in under 30 seconds vs ~90 for kind. On a dev machine you often create and destroy clusters multiple times — this adds up quickly.

**Lower resource footprint.** k3s strips out in-tree cloud provider code and uses containerd directly. On a 4-core / 8 GB machine (the minimum spec for this deployment), k3d leaves more headroom for the actual application workloads.

**Closer to production on-prem.** k3s (the distribution k3d wraps) is a popular choice for on-premises edge/enterprise Kubernetes. YellowPad's target clients run on-prem, so testing against k3s reduces the chance of surprises in the field.

**Built-in components to replace.** k3d ships Traefik and Klipper out of the box — replacing both with Cilium demonstrates that we own the full networking stack, which is the point of the exercise.

---

## When to prefer kind

- You need to test against **upstream Kubernetes** exactly (e.g., validating admission webhooks, specific API behaviour)
- Your CI system already uses kind and you want consistency
- You're already familiar with kind and the slight resource overhead doesn't matter
- You want the simplest possible Cilium config (one flag vs three)

---

## Command equivalents

| Action | kind | k3d |
|---|---|---|
| Create cluster | `kind create cluster --config kind-config.yaml` | `k3d cluster create --config k3d-config.yaml` |
| Delete cluster | `kind delete cluster --name yellowpad` | `k3d cluster delete yellowpad` |
| Load image | `kind load docker-image img:tag --name yellowpad` | `k3d image import img:tag --cluster yellowpad` |
| List clusters | `kind get clusters` | `k3d cluster list` |
| Get kubeconfig | automatic on create | automatic on create |
