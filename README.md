# One-Click Deployment Platform

## How It Works

```
User clicks "Deploy"
      │
      ▼
Backend API (FastAPI)
 ├─ Creates Kubernetes namespace   ← once per user
 ├─ Sets ResourceQuota + LimitRange
 ├─ Sets NetworkPolicy (namespace isolation)
 └─ Commits YAML to GitHub        ← every deploy
           │
           ▼ (within ~60 seconds)
      FluxCD detects git change
           │
           ▼
      Applies manifests to EKS namespace
           │
           ▼
      Pods start running
           │
           ▼
      Frontend polls /api/status → shows live pod state
```

## Key Design Decisions

| Decision | Reason |
|---|---|
| Terraform runs ONCE | Creates shared EKS cluster, node groups, FluxCD, Ingress, Monitoring |
| All users share the cluster | Cost-efficient; isolated by namespaces |
| Namespace per user | RBAC, NetworkPolicy, ResourceQuota isolation |
| FluxCD GitOps | No `kubectl apply` in CI — git is the source of truth |
| No Docker builds | Users upload pre-written YAML pointing at public/pre-built images |
| FastAPI backend | Creates namespaces + commits YAML; stateless except SQLite |

## One-Time Setup

```bash
export GITHUB_ORG=your-org
export GITHUB_REPO=gitops-deployments
export GITHUB_TOKEN=ghp_xxx
export AWS_REGION=us-east-1

chmod +x bootstrap.sh
./bootstrap.sh
```

## User Flow

1. Open `frontend/index.html`
2. Register → get User ID + namespace created in EKS
3. Write a Kubernetes YAML (Deployment + Service)
4. Upload it → backend commits to GitHub
5. FluxCD reconciles → pods run in `user-<name>` namespace
6. Status tab shows live pod state


# One-Click Ops – Architecture

## Platform Bootstrap (One-Time Setup)

```text
┌──────────────────────────────────────────────────────────────┐
│                    ONCE (bootstrap.sh)                      │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  Terraform provisions shared infrastructure:                 │
│                                                              │
│   ┌──────────────────────────────────────────────────────┐   │
│   │                 Amazon EKS Cluster                  │   │
│   ├──────────────────────────────────────────────────────┤   │
│   │ • Shared Kubernetes Cluster                         │   │
│   │ • Managed Node Group (EC2 t3.medium × 3)            │   │
│   │ • FluxCD Controllers                                │   │
│   │ • ingress-nginx with AWS NLB                        │   │
│   │ • Prometheus + Grafana Monitoring Stack             │   │
│   └──────────────────────────────────────────────────────┘   │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

---

## Per-User Deployment Flow

```text
┌──────────────────────────────────────────────────────────────┐
│                 PER USER (Frontend → API)                   │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  1. User Registration                                        │
│                                                              │
│     Namespace Created in EKS:                                │
│       user-alice                                              │
│                                                              │
│     Security & Resource Controls:                            │
│       • ResourceQuota (2 CPU, 2Gi RAM)                       │
│       • LimitRange (default requests/limits)                 │
│       • NetworkPolicy (namespace isolation)                  │
│                                                              │
│  2. Application Deployment                                   │
│                                                              │
│     Deployment YAML committed to GitHub:                     │
│                                                              │
│     clusters/production/users/user-alice/                    │
│                                                              │
│  3. GitOps Synchronization                                   │
│                                                              │
│     FluxCD watches repository changes                        │
│            │                                                 │
│            ▼                                                 │
│     Automatically applies manifests to EKS                   │
│                                                              │
│  4. Deployment Status Tracking                               │
│                                                              │
│     Backend polls:                                           │
│       • FluxCD API                                           │
│       • Kubernetes API                                       │
│                                                              │
│     Frontend displays:                                       │
│       • Pod Status                                           │
│       • Deployment Health                                    │
│       • Running Services                                     │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

---

# Architecture Diagram



```markdown
![One Click Ops Architecture](./docs/architecture.png)
```

If image is in root folder:

```markdown
![Architecture](./architecture.png)
```

If using GitHub URL directly:

```markdown
![Architecture](https://raw.githubusercontent.com/<username>/<repo>/main/docs/architecture.png)
```
