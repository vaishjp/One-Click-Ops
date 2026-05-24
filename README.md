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


┌─────────────────────────────────────────────────────────┐
│                    ONCE (bootstrap.sh)                   │
│                                                         │
│  Terraform ──► EKS Cluster (shared by all users)        │
│                 ├── Node Group (EC2 t3.medium x3)       │
│                 ├── FluxCD controllers                   │
│                 ├── ingress-nginx (NLB)                  │
│                 └── Prometheus + Grafana                 │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│              PER USER (frontend → API)                   │
│                                                         │
│  Register ──► namespace "user-alice" created in EKS     │
│               ├── ResourceQuota (2 CPU, 2Gi RAM)        │
│               ├── LimitRange (default limits)           │
│               └── NetworkPolicy (namespace isolation)   │
│                                                         │
│  Deploy ──► YAML committed to GitHub                    │
│              clusters/production/users/user-alice/      │
│                                                         │
│  FluxCD ──► detects commit ──► applies to EKS           │
│                                                         │
│  Status ──► polls FluxCD + Kubernetes API               │
│              ──► shows pod status in frontend           │
└─────────────────────────────────────────────────────────┘
