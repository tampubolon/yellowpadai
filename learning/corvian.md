# Architecture Overview

## Background

Corvian is a Canadian precision agriculture company that provides AI-powered crop advisory services to large-scale farming operations. Its platform ingests real-time data from field sensors — soil probes, weather stations, drone imagery — runs ML inference for yield prediction, crop disease detection, and irrigation scheduling, and surfaces recommendations through a web dashboard used by agronomists and farm managers.

Corvian's primary product is delivered as SaaS, but a growing segment of its enterprise clients — particularly state-owned agricultural enterprises (SOEs) and large cooperatives in Southeast Asia and Latin America — require on-premises deployments due to:

- **Data sovereignty regulations**: Indonesia's Government Regulation No. 71/2019 on Electronic System Operations prohibits agricultural production and sensor data from being processed outside national borders. Similar requirements apply under Brazil's LGPD and Vietnam's Cybersecurity Law 2018.
- **Remote farm locations**: Plantations across Kalimantan and Sumatra operate in areas where reliable cellular or satellite connectivity is unavailable. Field operations cannot depend on an internet connection for real-time analytics.
- **Corporate security posture**: State-owned enterprises in the agricultural sector classify yield data and production forecasts as commercially sensitive. Transmitting this data to a third-party cloud is prohibited by internal policy regardless of regulatory requirements.

### Reference Deployment: AgriNusa

The architecture in this document is modelled on a real engagement with **AgriNusa** — a state-owned Indonesian agricultural cooperative managing 480,000 hectares of palm oil and rice production across Sumatra and Kalimantan. AgriNusa's estate network spans:

- **12 estate offices** with stable internet connections → **Hybrid deployment**
- **38 remote field stations** with no internet access → **Air-gapped on-premise deployment**
- **Central analytics team** in Jakarta consuming aggregated data → **Cloud deployment**

Each field station runs a set of Corvian-supplied IoT sensors (soil moisture probes, micro-weather stations, leaf wetness sensors) that publish readings over MQTT to a local broker. The Corvian platform processes these readings locally, runs ML inference, and stores agronomic recommendations for field agronomists — all without requiring an internet connection.

---

## Core Components

| Component | Technology | Role |
|---|---|---|
| Frontend (FE) | Dash + Nginx | Field intelligence dashboard for agronomists and farm managers |
| Backend (BE) | FastAPI | Agronomic recommendation engine API |
| ML Service | FastAPI + model serving | Yield prediction, crop disease detection, soil health scoring |
| Message Broker | Eclipse Mosquitto (MQTT) | Ingests real-time data from field IoT sensors |
| Database | PostgreSQL 14 + TimescaleDB | Field sensor time-series data, crop records, recommendations |
| Metrics | Prometheus + Grafana | Infrastructure and application metrics |
| Logging | Loki + Promtail + Grafana | Log aggregation and search |

---

## 0. Container Image Build & Distribution Pipeline

Before any customer deployment, Corvian runs an internal CI/CD pipeline to build, test, package, and securely distribute versioned release archives. This section describes that pipeline.

### 0.1 Internal CI/CD Pipeline

The pipeline consists of five stages:

1. **Source → Binary**: CI compiles source code into binary executables. Source code is never loaded into Docker images, protecting Corvian's proprietary agronomic models and recommendation algorithms from exposure to clients or their staff.
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
corvian-release-v{VERSION}/
├── images/
│   ├── corvian-frontend-v{VERSION}.tar.gz
│   ├── corvian-backend-v{VERSION}.tar.gz
│   ├── corvian-ml-v{VERSION}.tar.gz
│   ├── corvian-sync-monitor-v{VERSION}.tar.gz
│   ├── eclipse-mosquitto-2.0.tar.gz
│   ├── postgres-14-timescaledb.tar.gz
│   ├── nginx-1.25-alpine.tar.gz
│   ├── prometheus-v2.48.tar.gz
│   ├── grafana-10.tar.gz
│   ├── loki-2.9.tar.gz
│   └── promtail-2.9.tar.gz
├── docker-compose.yml
├── docker-compose.hybrid.yml
├── .env.template
├── mosquitto/
│   └── mosquitto.conf
├── load-images.sh
├── checksums.sha256
└── INSTALL.md
```

### 0.3 Archive Packaging and Integrity

```bash
# Save images
docker save corvian-backend:v1.2.3 | gzip > images/corvian-backend-v1.2.3.tar.gz

# Generate checksums
sha256sum images/*.tar.gz > checksums.sha256

# Bundle
tar -czf corvian-release-v1.2.3.tar.gz corvian-release-v1.2.3/
```

### 0.4 Image Signing (Supply Chain Security)

All images are signed with [cosign](https://docs.sigstore.dev/cosign/overview/) before bundling, providing cryptographic proof of origin:

```bash
cosign sign --key cosign.key corvian-backend:v1.2.3
# Customer verifies with:
cosign verify --key cosign.pub corvian-backend:v1.2.3
```

### 0.5 Secure Archive Distribution

**Correction from original:** The original document suggested transmitting the encryption password via SMS. SMS is unencrypted and trivially interceptable by mobile carriers, SS7 attacks, or SIM-swap fraud. This is not acceptable for a security-conscious deployment carrying proprietary software.

**Correct approach: asymmetric GPG encryption.** The archive is encrypted with the customer's public GPG key. No shared password is ever transmitted — the customer decrypts using their own private key, which never leaves their possession.

```bash
# Corvian side: encrypt with AgriNusa's public key
gpg --import agrinusa-pubkey.asc
gpg --encrypt --recipient "it-security@agrinusa.id" \
    corvian-release-v1.2.3.tar.gz

# AgriNusa IT side: decrypt with their private key (no password exchange needed)
gpg --decrypt corvian-release-v1.2.3.tar.gz.gpg > corvian-release-v1.2.3.tar.gz

# Verify integrity
sha256sum -c checksums.sha256
```

Delivery method depends on the site:
- **Physical drive (air-gapped field stations)**: A Corvian field technician delivers an encrypted USB drive in person and supervises the initial deployment on-site. For the 38 remote AgriNusa field stations, this is the only viable distribution channel — there is no internet connection at any point in the process.
- **Secure S3 presigned URL with short TTL (4-hour window)**: for the 12 estate offices that have stable internet, Corvian generates a time-limited presigned URL after the release passes QA. The URL is sent over encrypted email; the download window is narrow enough to prevent opportunistic access.

---

## 1. On-Premise Deployment (Air-Gapped)

### 1.1 Prerequisites

- Docker Engine 24+ and Docker Compose v2+ installed (an offline installer is included in the release archive)
- Linux host with at least 8 vCPU, 16 GB RAM, and 200 GB disk
- Field IoT sensors (soil probes, micro-weather stations) pre-configured to publish MQTT messages to the server IP on port 1883. Corvian provides sensor firmware and configuration tooling separately.

### 1.2 Image Loading

**This step is required before starting services.** Docker images must be loaded from the archive into the local Docker daemon before `docker compose up` can use them.

```bash
# Extract the release archive
tar -xzf corvian-release-v1.2.3.tar.gz
cd corvian-release-v1.2.3/

# Verify integrity first
sha256sum -c checksums.sha256

# Load all images (or use the included load-images.sh)
for f in images/*.tar.gz; do
  docker load -i "$f"
done
```

### 1.3 Core Services

The on-premise stack consists of the following services:

1. **MQTT Broker (Mosquitto)** — receives real-time telemetry from field IoT sensors (soil moisture, temperature, leaf wetness, rainfall) on port 1883. The backend subscribes to sensor topics and persists readings to the time-series database.
2. **FastAPI Backend** — runs the agronomic recommendation engine, exposed on port 8000. Subscribes to MQTT topics, triggers ML inference, and writes spray, irrigation, and fertilization recommendations to the database.
3. **ML Service** — runs pre-trained models for yield prediction, crop disease detection (using field imagery), and soil health scoring on port 8080. Model weights are mounted as a read-only volume and updated with each Corvian release.
4. **Dashboard Frontend** — Dash application served behind Nginx, accessible on port 8050. Shows field-level sensor readings, ML-generated recommendations, and agronomist notes.
5. **PostgreSQL 14 + TimescaleDB** — persistent storage for all field data. TimescaleDB is used for the sensor time-series tables (automatic partitioning by time, efficient range queries). Volume-mapped for durability.
6. **Nginx Reverse Proxy** — routes external traffic, terminates TLS, and load-balances across BE replicas
7. **Prometheus + Grafana** — collects and visualizes infrastructure and application metrics
8. **Loki + Promtail** — log aggregation and search

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
  # ── IoT ingestion ────────────────────────────────────────────────────────────
  mosquitto:
    image: eclipse-mosquitto:2.0
    container_name: corvian-mosquitto
    ports:
      - "1883:1883"    # MQTT (field sensors connect here)
    volumes:
      - ./mosquitto/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro
      - mosquitto-data:/mosquitto/data
    networks:
      - sensor-net
    deploy:
      resources:
        limits:
          cpus: '0.25'
          memory: 64M

  # ── Application tier ─────────────────────────────────────────────────────────
  nginx:
    image: nginx:1.25-alpine
    container_name: corvian-nginx
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
    image: corvian-frontend:latest
    container_name: corvian-frontend
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
    image: corvian-backend:latest
    container_name: corvian-backend
    environment:
      - DATABASE_URL=postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}
      - ML_SERVICE_URL=http://ml-service:8080
      - MQTT_BROKER_URL=mqtt://mosquitto:1883
      - MQTT_SENSOR_TOPIC=agrinusa/+/sensors/#
    depends_on:
      postgres:
        condition: service_healthy
      mosquitto:
        condition: service_started
    networks:
      - frontend-net
      - backend-net
      - sensor-net
    deploy:
      resources:
        limits:
          cpus: '2.0'
          memory: 1G

  ml-service:
    image: corvian-ml:latest
    container_name: corvian-ml
    volumes:
      - ./models:/app/models:ro   # model weights, updated per release
    networks:
      - backend-net
    deploy:
      resources:
        limits:
          cpus: '4.0'
          memory: 4G

  # ── Data tier ────────────────────────────────────────────────────────────────
  postgres:
    image: timescale/timescaledb:2.13.0-pg14  # TimescaleDB for sensor time-series
    container_name: corvian-postgres
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

  # ── Monitoring & logging ─────────────────────────────────────────────────────
  prometheus:
    image: prom/prometheus:v2.48.0
    container_name: corvian-prometheus
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
    container_name: corvian-loki
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
    container_name: corvian-promtail
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
    container_name: corvian-grafana
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
  sensor-net:
    driver: bridge   # IoT sensors → Mosquitto → Backend
  frontend-net:
    driver: bridge   # Nginx → Frontend, Nginx → Backend
  backend-net:
    driver: bridge   # Backend → ML Service, Backend → Postgres
  monitoring-net:
    driver: bridge   # Prometheus, Loki, Grafana

volumes:
  mosquitto-data:
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
POSTGRES_USER=corvian
POSTGRES_PASSWORD=<change-me>
POSTGRES_DB=corvian_field_db
GRAFANA_USER=admin
GRAFANA_PASSWORD=<change-me>
CLOUD_RDS_ENDPOINT=                  # hybrid only — leave blank for air-gapped
```

### 1.8 Data Security

**1. Disk Encryption**

**Correction from original:** Docker has no native volume encryption capability. Encrypting at the Docker layer is not possible. Encryption must be applied at the **OS/filesystem level** using LUKS (Linux Unified Key Setup) before Docker ever mounts the volume:

```bash
# Encrypt a dedicated partition for Docker volumes
cryptsetup luksFormat /dev/sdb
cryptsetup open /dev/sdb corvian-data
mkfs.ext4 /dev/mapper/corvian-data
mount /dev/mapper/corvian-data /var/lib/docker/volumes
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

The cloud deployment serves clients like AgriNusa's central analytics team in Jakarta — they have reliable internet, want managed infrastructure, and need to aggregate data from all estate offices for national-level reporting. Corvian's cloud offering is designed around the realities of a small engineering team:

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
| Public ALB | Internet-facing | AgriNusa analysts → Frontend (ECS) |
| Internal ALB | Private subnet | Frontend → Backend (ECS) |

The backend connects to **RDS directly** via its DNS endpoint (e.g., `corvian-agrinusa.cluster-xxx.ap-southeast-1.rds.amazonaws.com`). No load balancer is needed or appropriate between the application tier and the database.

### 2.4 Cross-Account Image Distribution

Corvian builds and signs images in its own AWS account, then replicates them to each customer's ECR registry:

```
Corvian ECR  →  ECR Replication Policy  →  AgriNusa ECR  →  ECS Task Pull
```

Configure ECR cross-account replication on the Corvian source account:

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

The hybrid deployment targets AgriNusa's 12 estate offices — locations with intermittent satellite internet that are online often enough to benefit from cloud synchronization and central reporting, but must continue operating fully offline when the connection drops. A Kalimantan estate office might lose connectivity for 2–3 days during poor weather; the platform must continue serving field agronomists without interruption.

- **Cloud side**: identical to Section 2 (ECS + RDS + ALB + CloudWatch) — hosted in `ap-southeast-1` (Singapore) to minimize latency from Indonesian sites
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
-- On-premise PostgreSQL: publish all agronomic and sensor data tables
CREATE PUBLICATION corvian_pub FOR TABLE
  field_zones,
  weather_readings,
  soil_samples,
  spray_events,
  yield_records,
  ml_recommendations;

-- Cloud RDS: subscribe (auto-connects when VPN is up, resumes on reconnect)
CREATE SUBSCRIPTION corvian_sub
  CONNECTION 'host=10.10.0.2 dbname=corvian_field_db user=replicator password=xxx'
  PUBLICATION corvian_pub;
```

WAL segments are retained on-premise until the cloud subscriber confirms receipt. Because AgriNusa estate offices can be offline for up to 72 hours during poor weather, `wal_keep_size` should be set to at least 2 GB to avoid WAL being recycled before the subscriber reconnects. When the VPN reconnects, replication resumes without manual intervention.

**Conflict resolution:**

- Use a **cloud-first** strategy: cloud RDS is the authoritative source of truth for rows written by both sides simultaneously
- Apply an `updated_at` timestamp column to all replicated tables and set `REPLICA IDENTITY FULL` to expose full row images for conflict detection
- For conflicting rows, keep the row with the newer `updated_at` timestamp (last-write-wins)

### 3.4 Updated docker-compose.yml (Hybrid Additions)

In addition to all services defined in Section 1.4, the hybrid deployment adds a `sync-monitor` sidecar that tracks VPN health and replication lag:

```yaml
  sync-monitor:
    image: corvian-sync-monitor:latest
    container_name: corvian-sync-monitor
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
| `fieldops.agrinusa.corvian.io` | A | Estate office public IP (or NAT) | PRIMARY |
| `fieldops.agrinusa.corvian.io` | A | AWS Public ALB (ap-southeast-1) | SECONDARY (failover) |

Route 53 health checks poll the on-premise endpoint every 30 seconds. On health check failure, DNS automatically resolves to the AWS ALB in Singapore. The TTL should be set to 60 seconds to limit the client caching window during a failover event.

**Note on in-flight requests during failover:** During the DNS propagation window (up to one TTL interval), some clients may still route requests to the on-premise server while others have resolved to the cloud. To prevent session loss, the application must be stateless at the HTTP layer — session state should be stored in the database or a shared cache (e.g., Redis), not in application memory. This ensures requests can resume on either environment without requiring re-authentication.

**Local DNS fallback** (for farm clients when internet connectivity is fully unavailable):

Run a local DNS resolver — dnsmasq or CoreDNS deployed as a container — that resolves `fieldops.agrinusa.corvian.io` to the on-premise IP address. Configure all estate office laptops and tablets to use this local resolver as their primary DNS server. When the internet is unavailable, AgriNusa agronomists continue to access the Corvian dashboard without any manual intervention or reconfiguration.

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
