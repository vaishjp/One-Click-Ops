# ────────────────────────────────────────────────────────────
# FluxCD — GitOps engine
# ────────────────────────────────────────────────────────────

# Generate an SSH key pair; Flux uses this to pull from GitHub
resource "tls_private_key" "flux" {
  algorithm   = "ECDSA"
  ecdsa_curve = "P256"
}

# Add the public key as a deploy key on the GitHub repo
resource "github_repository_deploy_key" "flux" {
  title      = "flux-${var.cluster_name}"
  repository = var.github_repo
  key        = tls_private_key.flux.public_key_openssh
  read_only  = false   # Flux needs write access to push status commits
}

# Bootstrap Flux onto the EKS cluster
# This installs the Flux controllers and creates the GitRepository + Kustomization CRs
resource "flux_bootstrap_git" "main" {
  path = "clusters/production"

  components_extra = ["image-reflector-controller", "image-automation-controller"]

  depends_on = [
    aws_eks_node_group.main,
    github_repository_deploy_key.flux,
  ]
}

# ── Ingress-NGINX via Helm (managed by Flux HelmRelease) ─────
# We create the HelmRepository and HelmRelease CRs in the git
# repo; Flux will apply them. But we also need the namespace.
resource "kubernetes_namespace" "ingress_nginx" {
  metadata { name = "ingress-nginx" }
  depends_on = [aws_eks_cluster.main]
}

resource "kubernetes_namespace" "monitoring" {
  metadata { name = "monitoring" }
  depends_on = [aws_eks_cluster.main]
}

# Store the GitHub token as a Kubernetes secret so the backend
# API can commit YAML files on behalf of users
resource "kubernetes_secret" "github_credentials" {
  metadata {
    name      = "github-credentials"
    namespace = var.platform_namespace
  }

  data = {
    token = var.github_token
    org   = var.github_org
    repo  = var.github_repo
  }

  depends_on = [kubernetes_namespace.platform]
}

# Store kubeconfig secret so backend can call kubectl
resource "kubernetes_secret" "platform_sa_token" {
  metadata {
    name      = "platform-sa-token"
    namespace = var.platform_namespace
    annotations = {
      "kubernetes.io/service-account.name" = kubernetes_service_account.platform.metadata[0].name
    }
  }
  type       = "kubernetes.io/service-account-token"
  depends_on = [kubernetes_service_account.platform]
}

# Service account + ClusterRole for the backend API
resource "kubernetes_service_account" "platform" {
  metadata {
    name      = "deployment-platform"
    namespace = var.platform_namespace
  }
  depends_on = [kubernetes_namespace.platform]
}

resource "kubernetes_cluster_role" "platform" {
  metadata { name = "deployment-platform" }

  # Manage namespaces (create per-user namespaces)
  rule {
    api_groups = [""]
    resources  = ["namespaces"]
    verbs      = ["get", "list", "create", "delete"]
  }
  # Manage resource quotas and network policies inside namespaces
  rule {
    api_groups = [""]
    resources  = ["resourcequotas", "limitranges"]
    verbs      = ["get", "list", "create", "update", "delete"]
  }
  rule {
    api_groups = ["networking.k8s.io"]
    resources  = ["networkpolicies"]
    verbs      = ["get", "list", "create", "update", "delete"]
  }
  # Read pod/deployment status for the status API
  rule {
    api_groups = ["apps"]
    resources  = ["deployments", "replicasets"]
    verbs      = ["get", "list"]
  }
  rule {
    api_groups = [""]
    resources  = ["pods", "pods/log", "services", "events"]
    verbs      = ["get", "list"]
  }
  # Read Flux Kustomization status
  rule {
    api_groups = ["kustomize.toolkit.fluxcd.io"]
    resources  = ["kustomizations"]
    verbs      = ["get", "list", "create", "update", "delete"]
  }
  rule {
    api_groups = ["source.toolkit.fluxcd.io"]
    resources  = ["gitrepositories"]
    verbs      = ["get", "list"]
  }
}

resource "kubernetes_cluster_role_binding" "platform" {
  metadata { name = "deployment-platform" }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = kubernetes_cluster_role.platform.metadata[0].name
  }

  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account.platform.metadata[0].name
    namespace = var.platform_namespace
  }
}