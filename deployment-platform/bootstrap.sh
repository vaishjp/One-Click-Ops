#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# bootstrap.sh — ONE-TIME setup script
# Run this once to provision the entire platform.
# All subsequent user deployments happen through the frontend.
# ──────────────────────────────────────────────────────────────
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; NC='\033[0m'; BOLD='\033[1m'

info()    { echo -e "${CYAN}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC}   $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERR]${NC}  $*"; exit 1; }
step()    { echo -e "\n${BOLD}━━━ $* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"; }

# ── Prerequisites check ───────────────────────────────────────

step "Checking prerequisites"

for cmd in terraform aws kubectl helm git flux; do
  if command -v "$cmd" &>/dev/null; then
    success "$cmd found ($(${cmd} version --client 2>/dev/null | head -1 || true))"
  else
    error "$cmd not found — install it before running this script"
  fi
done

# ── Variables — edit these ────────────────────────────────────

step "Configuration"

: "${AWS_REGION:=us-east-1}"
: "${CLUSTER_NAME:=deployment-platform}"
: "${GITHUB_ORG:?Set GITHUB_ORG env var}"
: "${GITHUB_REPO:?Set GITHUB_REPO env var}"
: "${GITHUB_TOKEN:?Set GITHUB_TOKEN env var}"

info "AWS Region:   $AWS_REGION"
info "Cluster:      $CLUSTER_NAME"
info "GitHub:       $GITHUB_ORG/$GITHUB_REPO"

# ── Step 1: Create GitHub repo if it doesn't exist ───────────

step "Step 1 — GitHub GitOps Repository"

if gh repo view "$GITHUB_ORG/$GITHUB_REPO" &>/dev/null 2>&1; then
  success "Repository $GITHUB_ORG/$GITHUB_REPO already exists"
else
  info "Creating repository…"
  gh repo create "$GITHUB_ORG/$GITHUB_REPO" --private --description "GitOps manifests"
  success "Created $GITHUB_ORG/$GITHUB_REPO"
fi

# Push initial structure
TMP=$(mktemp -d)
git clone "https://${GITHUB_TOKEN}@github.com/${GITHUB_ORG}/${GITHUB_REPO}.git" "$TMP"
mkdir -p "$TMP/clusters/production/infrastructure"
mkdir -p "$TMP/clusters/production/users"

cp gitops/infrastructure/ingress-nginx.yaml "$TMP/clusters/production/infrastructure/"
cp gitops/infrastructure/prometheus.yaml    "$TMP/clusters/production/infrastructure/"
cp gitops/infrastructure/kustomization.yaml "$TMP/clusters/production/infrastructure/"
touch "$TMP/clusters/production/users/.gitkeep"

cd "$TMP"
git add -A
git diff --staged --quiet || git commit -m "feat: initial GitOps structure"
git push origin main || true
cd -
rm -rf "$TMP"
success "Initial GitOps structure committed"

# ── Step 2: Terraform ─────────────────────────────────────────

step "Step 2 — Terraform (EKS + FluxCD)"

cd terraform/

# Write tfvars
cat > terraform.tfvars <<EOF
aws_region   = "$AWS_REGION"
cluster_name = "$CLUSTER_NAME"
github_org   = "$GITHUB_ORG"
github_repo  = "$GITHUB_REPO"
github_token = "$GITHUB_TOKEN"
EOF

info "terraform init…"
terraform init -upgrade

info "terraform plan…"
terraform plan -out=tfplan

echo ""
warn "Review the plan above. Press ENTER to apply or Ctrl+C to abort."
read -r

info "terraform apply…"
terraform apply tfplan

success "Infrastructure provisioned"

# ── Step 3: Configure kubectl ─────────────────────────────────

step "Step 3 — Configure kubectl"

aws eks update-kubeconfig \
  --name "$CLUSTER_NAME" \
  --region "$AWS_REGION"

kubectl get nodes
success "kubectl configured"

# ── Step 4: Wait for FluxCD ───────────────────────────────────

step "Step 4 — Wait for FluxCD to become ready"

info "Waiting for Flux controllers…"
kubectl -n flux-system rollout status deployment/source-controller      --timeout=5m
kubectl -n flux-system rollout status deployment/kustomize-controller   --timeout=5m
kubectl -n flux-system rollout status deployment/helm-controller        --timeout=5m
kubectl -n flux-system rollout status deployment/notification-controller --timeout=5m

flux get kustomizations --watch &
FLUX_PID=$!
sleep 30
kill $FLUX_PID 2>/dev/null || true

success "FluxCD is running"

# ── Step 5: Wait for Ingress + Monitoring ────────────────────

step "Step 5 — Wait for infrastructure HelmReleases"

info "Waiting for ingress-nginx HelmRelease…"
kubectl -n ingress-nginx wait helmrelease/ingress-nginx \
  --for=condition=Ready --timeout=10m 2>/dev/null || \
  warn "Helm release not ready yet — Flux may still be reconciling"

info "Ingress LB address:"
kubectl -n ingress-nginx get svc ingress-nginx-controller \
  -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true

# ── Step 6: Deploy backend API ────────────────────────────────

step "Step 6 — Deploy Backend API"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/deployment-platform-api"

# Create ECR repo
aws ecr describe-repositories --repository-names deployment-platform-api \
  --region "$AWS_REGION" &>/dev/null || \
  aws ecr create-repository --repository-name deployment-platform-api \
    --region "$AWS_REGION"

# Build and push
info "Building backend Docker image…"
aws ecr get-login-password --region "$AWS_REGION" | \
  docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

cd ../backend
docker build -t deployment-platform-api .
docker tag deployment-platform-api:latest "${ECR_URI}:latest"
docker push "${ECR_URI}:latest"

# Patch image in manifest and apply
sed "s|YOUR_ECR_REPO|${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com|g" \
  ../k8s-manifests/backend.yaml | kubectl apply -f -

kubectl -n deployment-platform rollout status deployment/deployment-platform-api --timeout=5m
success "Backend API deployed"

# ── Done ──────────────────────────────────────────────────────

step "Bootstrap Complete 🎉"

INGRESS_LB=$(kubectl -n ingress-nginx get svc ingress-nginx-controller \
  -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || echo "pending")

echo -e "
${GREEN}Platform is live!${NC}

  Ingress LB:     ${CYAN}${INGRESS_LB}${NC}
  Backend API:    ${CYAN}http://${INGRESS_LB}/api${NC}
  Grafana:        ${CYAN}http://${INGRESS_LB}:3000${NC}

  Next steps:
  1. Point your domain DNS → ${INGRESS_LB}
  2. Open frontend/index.html in your browser
     (or serve it: cd frontend && python3 -m http.server 3001)
  3. Register a user → click Deploy → upload a YAML file

  Everything else is automated. FluxCD watches the GitHub repo
  and reconciles changes into EKS within ~60 seconds.
"