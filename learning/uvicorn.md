# Architecture Overview

This document describes the architecture of the Uni-Corn LLC corn optimization platform, covering the container image build and distribution pipeline, and all three supported deployment targets: on-premise air-gapped farms, cloud (AWS ECS), and hybrid configurations.

## Core Components

| Component | Technology | Role |
|---|---|---|
| Frontend (FE) | Dash + Nginx | Crop analytics dashboard |
| Backend (BE) | FastAPI | Corn optimization algorithms API |
| ML Service | FastAPI + model serving | Pre-trained model inference |
| Database | PostgreSQL 14 | Persistent data store |
| Metrics | Prometheus + Grafana | Infrastructure and app metrics |
| Logging | Loki + Promtail + Grafana | Log aggregation and search |

---

## 0. Container Image Build & Distribution Pipeline

Before any customer deployment, Uni-Corn LLC runs an internal CI/CD pipeline to build, test, package, and securely distribute versioned release archives. This section describes that pipeline.

### 0.1 Internal CI/CD Pipeline

The pipeline consists of five stages:

1. **Source → Binary**: CI compiles source code into binary executables. Source code is never loaded into Docker images, protecting Uni-Corn LLC's intellectual property.
2. **Binary → Docker Image**: A multi-stage Dockerfile is used. The build stage compiles or copies the binary; the final production stage contains only the binary and its runtime dependencies — no source code, no compiler toolchain.
3. **Image → Internal Harbor Registry**: Built images are pushed to an internal [Harbor](https://goharbor.io/) registry. Harbor is chosen over a plain Docker registry for:
   - Role-based access control (RBAC) per project and user
   - Automated image vulnerability scanning via Trivy
   - Image signing support (Notary / cosign)
   - Configurable retention policies to manage storage
4. **Automated Integration Tests**: The CI pipeline spins up all services using Docker Compose, runs smoke tests, and validates inter-service communication (FE → BE, BE → ML Service, BE → DB, monitoring pipelines).
5. **Release Archive Bundling**: On a successful test run, all images plus the Compose file, environment template, load script, checksums, and installation docs are bundled into a versioned release archive.

### 0.2 Release Archive Structure

```
unicorn-release-v{VERSION}/
├── images/
│   ├── unicorn-frontend-v{VERSION}.tar
│   ├── unicorn-backend-v{VERSION}.tar
│   ├── unicorn-ml-v{VERSION}.tar
│   ├── postgres-14.tar
│   ├── nginx-1.25-alpine.tar
│   ├── prometheus-v2.48.tar
│   ├── grafana-10.tar
│   ├── loki-2.9.tar
│   └── promtail-2.9.tar
├── docker-compose.yml
├── .env.template
├── load-images.sh
├── checksums.sha256
└── INSTALL.md
```

### 0.3 Archive Packaging and Integrity

```bash
# Save images
docker save unicorn-backend:v1.2.3 | gzip > images/unicorn-backend-v1.2.3.tar.gz

# Generate checksums
sha256sum images/*.tar.gz > checksums.sha256

# Bundle
tar -czf unicorn-release-v1.2.3.tar.gz unicorn-release-v1.2.3/
```

### 0.4 Image Signing (Supply Chain Security)

All images are signed with [cosign](https://docs.sigstore.dev/cosign/overview/) before bundling, providing cryptographic proof of origin:

```bash
cosign sign --key cosign.key unicorn-backend:v1.2.3
# Customer verifies with:
cosign verify --key cosign.pub unicorn-backend:v1.2.3
```

### 0.5 Secure Archive Distribution

**Correction from original:** The original document suggested transmitting the encryption password via SMS. SMS is unencrypted and trivially interceptable by mobile carriers, SS7 attacks, or SIM-swap fraud. This is not acceptable for a security-conscious deployment carrying proprietary software.

**Correct approach: asymmetric GPG encryption.** The archive is encrypted with the customer's public GPG key. No shared password is ever transmitted — the customer decrypts using their own private key, which never leaves their possession.

```bash
# Uni-Corn side: encrypt with customer's public key
gpg --import customer-pubkey.asc
gpg --encrypt --recipient "customer@unicorn-client.com" \
    unicorn-release-v1.2.3.tar.gz

# Customer side: decrypt with their private key (no password exchange needed)
gpg --decrypt unicorn-release-v1.2.3.tar.gz.gpg > unicorn-release-v1.2.3.tar.gz

# Verify integrity
sha256sum -c checksums.sha256
```

The encrypted archive is uploaded to S3. Delivery method depends on the site:
- **Physical drive**: for truly air-gapped sites with no internet access at any point
- **Secure S3 presigned URL with short TTL**: for sites with occasional internet access during a maintenance window

---

## 1. On-Premise Deployment (Air-Gapped)

### 1.1 Prerequisites

- Docker Engine 24+ and Docker Compose v2+ installed (an offline installer is included in the release archive)
- Linux host with at least 8 vCPU, 16 GB RAM, and 200 GB disk

### 1.2 Image Loading

**This step is required before starting services.** Docker images must be loaded from the archive into the local Docker daemon before `docker compose up` can use them.

```bash
# Extract the release archive
tar -xzf unicorn-release-v1.2.3.tar.gz
cd unicorn-release-v1.2.3/

# Verify integrity first
sha256sum -c checksums.sha256

# Load all images (or use the included load-images.sh)
for f in images/*.tar.gz; do
  docker load -i "$f"
done
```

### 1.3 Core Services

The on-premise stack consists of the following services:

1. **FastAPI Backend** — runs the corn optimization algorithms, exposed on port 8000
2. **Dashboard Frontend** — Dash application served behind Nginx, accessible on port 8050
3. **ML Service** — pre-trained model inference service on port 8080, with model weights mounted as a read-only volume
4. **PostgreSQL** — persistent storage for crop data, recommendations, and sensor readings; volume-mapped for durability
5. **Nginx Reverse Proxy** — routes external traffic, terminates TLS, and load-balances across BE replicas
6. **Prometheus + Grafana** — collects and visualizes infrastructure and application metrics
7. **Loki + Promtail** — log aggregation and search

**Logging choice rationale — Loki vs ELK:**

ELK (Elasticsearch, Logstash, Kibana) requires approximately 4–8 GB of RAM for Elasticsearch alone, before accounting for any other workload. For air-gapped farm hardware with constrained resources, this is prohibitive. **Loki + Promtail** is the correct choice for this environment:

- Loki indexes only metadata (labels), not full log text, resulting in approximately 10x lower memory footprint than Elasticsearch
- Promtail scrapes Docker container logs automatically without additional configuration per service
- Grafana, which is already deployed for metrics, doubles as the Loki UI — no additional Kibana instance is needed
- A single Grafana instance handles both metrics (Prometheus datasource) and logs (Loki datasource), simplifying operations

### 1.4 docker-compose.yml

The following Compose file defines all services. It includes the ML service and the Loki/Promtail logging stack, which must be present for a complete deployment.

```yaml
version: '3.8'

services:
  nginx:
    image: nginx:1.25-alpine
    container_name: unicorn-nginx
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./nginx/certs:/etc/nginx/certs:ro
    depends_on:
      - frontend
      - backend
    networks:
      - frontend-net
    deploy:
      resources:
        limits:
          cpus: '0.5'
          memory: 128M

  frontend:
    image: unicorn-frontend:latest
    container_name: unicorn-frontend
    environment:
      - API_URL=http://backend:8000
    depends_on:
      - backend
    networks:
      - frontend-net
    deploy:
      resources:
        limits:
          cpus: '1.0'
          memory: 512M

  backend:
    image: unicorn-backend:latest
    container_name: unicorn-backend
    environment:
      - DATABASE_URL=postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}
      - ML_SERVICE_URL=http://ml-service:8080
    depends_on:
      postgres:
        condition: service_healthy
    networks:
      - frontend-net
      - backend-net
    deploy:
      resources:
        limits:
          cpus: '2.0'
          memory: 1G

  ml-service:
    image: unicorn-ml:latest
    container_name: unicorn-ml
    volumes:
      - ./models:/app/models:ro
    networks:
      - backend-net
    deploy:
      resources:
        limits:
          cpus: '4.0'
          memory: 4G

  postgres:
    image: postgres:14
    container_name: unicorn-postgres
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    volumes:
      - postgres-data:/var/lib/postgresql/data
      - ./postgres/init.sql:/docker-entrypoint-initdb.d/init.sql:ro
    networks:
      - backend-net
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER}"]
      interval: 10s
      timeout: 5s
      retries: 5
    deploy:
      resources:
        limits:
          cpus: '1.0'
          memory: 1G

  prometheus:
    image: prom/prometheus:v2.48.0
    container_name: unicorn-prometheus
    volumes:
      - ./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
    networks:
      - monitoring-net
      - backend-net
    deploy:
      resources:
        limits:
          cpus: '0.5'
          memory: 256M

  loki:
    image: grafana/loki:2.9.0
    container_name: unicorn-loki
    volumes:
      - ./loki/loki-config.yml:/etc/loki/local-config.yaml:ro
      - loki-data:/loki
    networks:
      - monitoring-net
    deploy:
      resources:
        limits:
          cpus: '0.5'
          memory: 256M

  promtail:
    image: grafana/promtail:2.9.0
    container_name: unicorn-promtail
    volumes:
      - /var/lib/docker/containers:/var/lib/docker/containers:ro
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./loki/promtail-config.yml:/etc/promtail/config.yml:ro
    networks:
      - monitoring-net
    deploy:
      resources:
        limits:
          cpus: '0.25'
          memory: 128M

  grafana:
    image: grafana/grafana:10.0.0
    container_name: unicorn-grafana
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_USER=${GRAFANA_USER}
      - GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_PASSWORD}
    volumes:
      - grafana-data:/var/lib/grafana
      - ./grafana/provisioning:/etc/grafana/provisioning:ro
    networks:
      - monitoring-net
    deploy:
      resources:
        limits:
          cpus: '0.5'
          memory: 256M

networks:
  frontend-net:
    driver: bridge
  backend-net:
    driver: bridge
  monitoring-net:
    driver: bridge

volumes:
  postgres-data:
  loki-data:
  grafana-data:
```

### 1.5 Nginx Configuration for Backend Scaling

**Correction from original:** The original stated the backend is "scalable by increasing replicas in docker-compose.yml." This is misleading. Docker Compose does not automatically load-balance HTTP traffic across replicas without a reverse proxy configured to do so. Nginx must be explicitly configured with an upstream block:

```nginx
upstream backend_pool {
    server backend:8000;
    # When running multiple replicas via `docker compose up --scale backend=3`,
    # add each replica's alias here, or use Docker's internal DNS round-robin
    # by pointing to the service name (Docker resolves to all containers).
}

server {
    listen 80;
    location /api/ {
        proxy_pass http://backend_pool;
    }
    location / {
        proxy_pass http://frontend:8050;
    }
}
```

Note: Docker's internal DNS already performs round-robin resolution across service replicas when using the service name. The upstream block makes this explicit and opens the door to health-check-based routing via the `ngx_http_upstream_module` directives.

### 1.6 Deployment Steps

```bash
# 1. Extract and verify archive (see Section 1.2)

# 2. Load images
bash load-images.sh

# 3. Configure environment
cp .env.template .env
# Edit .env with site-specific values

# 4. Start services
docker compose up -d

# 5. Validate
docker compose ps                          # all services healthy
curl http://localhost/api/health           # BE health check
curl http://localhost                      # FE reachable
curl http://localhost:3000                 # Grafana dashboard
```

### 1.7 Configuration (.env.template)

```env
POSTGRES_USER=unicorn
POSTGRES_PASSWORD=<change-me>
POSTGRES_DB=unicorn_db
GRAFANA_USER=admin
GRAFANA_PASSWORD=<change-me>
```

### 1.8 Data Security

**1. Disk Encryption**

**Correction from original:** Docker has no native volume encryption capability. Encrypting at the Docker layer is not possible. Encryption must be applied at the **OS/filesystem level** using LUKS (Linux Unified Key Setup) before Docker ever mounts the volume:

```bash
# Encrypt a dedicated partition for Docker volumes
cryptsetup luksFormat /dev/sdb
cryptsetup open /dev/sdb unicorn-data
mkfs.ext4 /dev/mapper/unicorn-data
mount /dev/mapper/unicorn-data /var/lib/docker/volumes
```

All Docker named volumes stored on this partition are encrypted at rest automatically, with no changes required to the Docker Compose configuration.

**2. Service Authentication**

Backend API endpoints are protected with JWT. Grafana admin credentials are configured via the `.env` file and must never be committed to version control.

**3. Transport Security**

Nginx terminates TLS using a self-signed certificate issued by an internal offline CA. Because the farm has no internet access, Let's Encrypt certificate issuance is not an option. Generate an internal CA and server certificate as follows:

```bash
# Generate offline CA and server cert
openssl req -x509 -newkey rsa:4096 -keyout ca.key -out ca.crt -days 3650 -nodes
openssl req -newkey rsa:4096 -keyout server.key -out server.csr -nodes
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -out server.crt -days 365
```

Distribute `ca.crt` to all client browsers and devices on the farm network so they trust the certificate without browser warnings.

### 1.9 Network Diagram

![On-premise deployment diagram](https://github.com/user-attachments/assets/bd192fd5-7765-4166-8e40-208c3c1443b6)

---

## 2. Cloud Deployment (AWS ECS)

### 2.1 Key Considerations

The cloud deployment is designed around the realities of a small engineering team at an early-stage startup:

- **Velocity**: Managed services reduce the operational surface area, allowing engineers to focus on product rather than infrastructure.
- **Reliability**: AWS-managed services (ECS, RDS, ALB) come with built-in high availability and SLAs.
- **Cost**: Pay-as-you-go pricing fits variable workloads; Fargate eliminates the need to manage and right-size EC2 fleets.
- **Technical debt**: Container-native deployment on ECS preserves the option to migrate to Kubernetes later without rewriting application code.

### 2.2 Design Decision: ECS over EKS over EC2

| Option | Pros | Cons |
|---|---|---|
| **ECS (chosen)** | Fully managed control plane; native AWS integrations (IAM, Secrets Manager, CloudWatch, ECR); low operational overhead; no cluster nodes to patch | Less portable than Kubernetes; fewer ecosystem tools |
| **EKS** | Industry-standard Kubernetes; large ecosystem; portable workloads | Significant operational overhead; control plane cost ~$73/month; requires Kubernetes expertise the team may not have |
| **EC2 (plain)** | Maximum control; lowest abstraction | High operational burden; manual patching, scaling, and health management |

ECS on Fargate is the right choice for a skeleton team that needs to ship quickly and reliably without dedicating headcount to cluster operations.

### 2.3 Architecture

The ECS deployment uses a standard AWS multi-tier VPC layout:

- **VPC** with public and private subnets across at least two availability zones
- **All ECS workloads** run in private subnets — they are not directly reachable from the internet
- **NAT Gateway** in the public subnet for outbound internet access (e.g., pulling from ECR, external API calls)
- **VPC Endpoints** for ECR API and ECR DKR — image pulls bypass the NAT Gateway entirely, reducing NAT costs and improving security by keeping image traffic within the AWS network

**Load Balancers**

**Correction from original:** The original described three load balancers, including one between the backend and the database. RDS does not sit behind a load balancer. AWS handles Multi-AZ failover transparently through a DNS CNAME that automatically updates on failover — no load balancer is involved. Using **two ALBs** is the correct architecture:

| ALB | Scope | Routes |
|---|---|---|
| Public ALB | Internet-facing | External users → Frontend (ECS) |
| Internal ALB | Private subnet | Frontend → Backend (ECS) |

The backend connects to **RDS directly** via its DNS endpoint (e.g., `unicorn-db.cluster-xxx.rds.amazonaws.com`). No load balancer is needed or appropriate between the application tier and the database.

### 2.4 Cross-Account Image Distribution

Uni-Corn LLC builds and signs images in its own AWS account, then replicates them to each customer's ECR registry:

```
Uni-Corn ECR  →  ECR Replication Policy  →  Customer ECR  →  ECS Task Pull
```

Configure ECR cross-account replication on the Uni-Corn source account:

```json
{
  "rules": [{
    "destinations": [{
      "region": "ap-southeast-1",
      "registryId": "<customer-account-id>"
    }]
  }]
}
```

The customer's ECS task definitions reference their own ECR registry. Images are scanned on push in the customer's ECR using ECR enhanced scanning (Inspector).

### 2.5 Secrets Management

Credentials are stored in **AWS Secrets Manager** — not as plaintext environment variables in ECS task definitions. This applies to:

- RDS credentials
- API keys and third-party tokens

The ECS task IAM role is granted `secretsmanager:GetSecretValue` permissions. Secrets are injected at container startup via the `secrets:` field in the task definition, and are never written to the task definition itself or visible in the ECS console.

### 2.6 CD Pipeline

A CD pipeline (AWS CodeDeploy or Jenkins) monitors the customer's ECR for new image tags. On a new push, it triggers an ECS service update using a rolling deployment strategy, ensuring zero downtime during updates. Rollback is automatic if health checks fail within the configured stabilization window.

### 2.7 Monitoring and Logging

**AWS CloudWatch** handles both metrics and logs for the cloud deployment. All ECS container logs are routed to CloudWatch Logs via the `awslogs` log driver in the task definition. Application and infrastructure metrics are published as CloudWatch custom metrics.

Rationale: for a skeleton team, the operational overhead of self-managing Prometheus, Grafana, and Loki in ECS is not justified. CloudWatch is fully managed, requires no infrastructure to operate, and integrates natively with ECS, RDS, and ALB. The higher cost relative to self-managed tooling is outweighed by the velocity benefit of zero infrastructure management.

### 2.8 Architecture Diagram

![Cloud deployment diagram](https://github.com/user-attachments/assets/2893ec17-4b4e-4d46-8c2d-ede93ffad9f2)

---

## 3. Hybrid Deployment

### 3.1 Overview

The hybrid deployment targets farms with intermittent internet connectivity. The two environments — on-premise and cloud — are designed to operate fully independently. Data synchronizes when a VPN connection is available.

- **Cloud side**: identical to Section 2 (ECS + RDS + ALB + CloudWatch)
- **On-premise side**: identical to Section 1 (Docker Compose) with additional services for VPN connectivity, data synchronization monitoring, and local DNS fallback

### 3.2 Connectivity: VPN Setup

Two options are available depending on the customer's network infrastructure:

**Option A — AWS Site-to-Site VPN** (suitable when the customer has a static public IP and a compatible router or firewall):

- Create a Virtual Private Gateway attached to the AWS VPC
- Create a Customer Gateway resource with the farm's public IP address
- Establish an IPsec tunnel between the two gateways
- Add route table entries in the private subnets to direct VPC CIDR traffic through the VPN

**Option B — WireGuard** (recommended for farms with simpler or dynamic network setups):

WireGuard is lighter than IPsec, significantly easier to configure, and handles NAT traversal well — a common requirement on farm networks that do not have static public IPs or enterprise-grade routers.

- Deploy WireGuard on the Docker host itself or a small dedicated VM
- Cloud side: a WireGuard peer runs on an EC2 instance in the private subnet

```bash
# On-premise WireGuard config example
[Interface]
PrivateKey = <farm-private-key>
Address = 10.10.0.2/24

[Peer]
PublicKey = <cloud-public-key>
Endpoint = <cloud-ec2-ip>:51820
AllowedIPs = 10.0.0.0/16   # AWS VPC CIDR
PersistentKeepalive = 25    # keeps tunnel alive through NAT
```

### 3.3 Data Synchronization

**Correction from original:** The original proposed AWS Database Migration Service (DMS) for data synchronization. **DMS is not suitable for intermittent connectivity.** DMS uses Change Data Capture (CDC) over a persistent, stable TCP connection. On a farm VPN that drops regularly, DMS replication tasks will error out repeatedly, require manual intervention to restart, and risk data loss or duplication at reconnection boundaries.

**Correct approach: PostgreSQL logical replication (pglogical) with local WAL buffering.**

PostgreSQL's Write-Ahead Log (WAL) durably records all changes locally. When the VPN tunnel is down, WAL segments accumulate on-premise. When the tunnel comes back up, the cloud RDS subscriber automatically resumes from exactly where it left off, replaying buffered WAL entries. This behavior is inherent to PostgreSQL logical replication and requires no custom reconnection logic.

```sql
-- On-premise PostgreSQL: create a publication for the relevant tables
CREATE PUBLICATION unicorn_pub FOR TABLE crops, sensor_readings, recommendations;

-- Cloud RDS: create a subscription (connects when VPN is up, reconnects automatically)
CREATE SUBSCRIPTION unicorn_sub
  CONNECTION 'host=10.10.0.2 dbname=unicorn_db user=replicator password=xxx'
  PUBLICATION unicorn_pub;
```

WAL segments are retained on-premise until the cloud subscriber confirms receipt. The `wal_keep_size` parameter controls how much WAL is retained during extended disconnections. When the VPN reconnects, replication resumes without manual intervention.

**Conflict resolution:**

- Use a **cloud-first** strategy: cloud RDS is the authoritative source of truth for rows written by both sides simultaneously
- Apply an `updated_at` timestamp column to all replicated tables and set `REPLICA IDENTITY FULL` to expose full row images for conflict detection
- For conflicting rows, keep the row with the newer `updated_at` timestamp (last-write-wins)

### 3.4 Updated docker-compose.yml (Hybrid Additions)

In addition to all services defined in Section 1.4, the hybrid deployment adds a `sync-monitor` sidecar that tracks VPN health and replication lag:

```yaml
  sync-monitor:
    image: unicorn-sync-monitor:latest
    container_name: unicorn-sync-monitor
    environment:
      - CLOUD_DB_HOST=${CLOUD_RDS_ENDPOINT}
      - LOCAL_DB_URL=postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}
      - VPN_PEER_IP=10.10.0.1
    depends_on:
      postgres:
        condition: service_healthy
    networks:
      - backend-net
    deploy:
      resources:
        limits:
          cpus: '0.25'
          memory: 128M
```

The `sync-monitor` service:
- Pings the VPN peer every 30 seconds to detect connectivity status
- Queries `pg_stat_replication` to report replication lag in bytes and seconds
- Exposes a `/sync/status` HTTP endpoint that Grafana polls to display sync health on the operations dashboard

### 3.5 Traffic Routing and DNS Failover

Route 53 **Failover Routing** directs users to the on-premise system by default and automatically fails over to the cloud ALB if the on-premise endpoint becomes unavailable.

| Record | Type | Target | Routing Policy |
|---|---|---|---|
| `app.unicorn-client.com` | A | On-premise public IP (or NAT) | PRIMARY |
| `app.unicorn-client.com` | A | AWS Public ALB | SECONDARY (failover) |

Route 53 health checks poll the on-premise endpoint every 30 seconds. On health check failure, DNS automatically resolves to the AWS ALB. The TTL should be set to 60 seconds to limit the client caching window during a failover event.

**Note on in-flight requests during failover:** During the DNS propagation window (up to one TTL interval), some clients may still route requests to the on-premise server while others have resolved to the cloud. To prevent session loss, the application must be stateless at the HTTP layer — session state should be stored in the database or a shared cache (e.g., Redis), not in application memory. This ensures requests can resume on either environment without requiring re-authentication.

**Local DNS fallback** (for farm clients when internet connectivity is fully unavailable):

Run a local DNS resolver — dnsmasq or CoreDNS deployed as a container — that resolves `app.unicorn-client.com` to the on-premise IP address. Configure all farm client machines to use this local resolver as their primary DNS server. When the internet is unavailable, farm workers continue to reach the on-premise system without any manual intervention or reconfiguration.

### 3.6 Architecture Diagram

![Hybrid deployment diagram](https://github.com/user-attachments/assets/3641a612-962d-4769-8820-8829d181eae6)

### 3.7 Hybrid DNS Failover Diagram

![Hybrid DNS failover](https://github.com/user-attachments/assets/0e08ddfa-1955-45ca-8b05-078acffe7e79)

---

## References

- [1] https://aws.amazon.com/eks/pricing/
- [2] https://aws.amazon.com/ecs/sla/
- [3] https://docs.aws.amazon.com/AmazonECR/latest/userguide/encryption-at-rest.html
- [4] https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Overview.Encryption.html#Overview.Encryption.Enabling
- [5] https://grafana.com/docs/loki/latest/
- [6] https://www.postgresql.org/docs/current/logical-replication.html
- [7] https://www.wireguard.com/
- [8] https://docs.sigstore.dev/cosign/overview/
