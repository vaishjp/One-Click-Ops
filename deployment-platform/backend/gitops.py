"""
GitOps service layer.
Commits user YAML files into the GitHub repository so FluxCD
picks them up and deploys them to the EKS cluster.

Directory layout in the repo:
  clusters/production/
    flux-system/          ← managed by Flux bootstrap
    infrastructure/       ← ingress, monitoring (committed manually / by TF)
    users/
      <namespace>/
        namespace.yaml    ← the Namespace manifest
        kustomization.yaml← FluxCD Kustomization CR (points at this dir)
        <deployment>.yaml ← user-uploaded manifests
"""

import base64
import logging
import os
from datetime import datetime
from typing import Optional

import yaml
from github import Github, GithubException

logger = logging.getLogger(__name__)

# ── Config from environment / Kubernetes secret ───────────────
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_ORG   = os.environ["GITHUB_ORG"]
GITHUB_REPO  = os.environ["GITHUB_REPO"]
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")

_gh   = Github(GITHUB_TOKEN)
_repo = _gh.get_repo(f"{GITHUB_ORG}/{GITHUB_REPO}")


def bootstrap_user_in_gitops(namespace: str, user_id: str, username: str):
    """
    Create the initial directory structure in the GitOps repo for a new user.
    Called once when a user registers.
    """
    # 1. Namespace manifest
    _upsert_file(
        path=f"clusters/production/users/{namespace}/namespace.yaml",
        content=_render_namespace_manifest(namespace, user_id, username),
        message=f"feat: bootstrap namespace for user {username}",
    )

    # 2. FluxCD Kustomization pointing at the user's directory
    _upsert_file(
        path=f"clusters/production/users/{namespace}/flux-kustomization.yaml",
        content=_render_flux_kustomization(namespace),
        message=f"feat: add FluxCD kustomization for namespace {namespace}",
    )

    # 3. Empty kustomization.yaml so kustomize knows what to include
    _upsert_file(
        path=f"clusters/production/users/{namespace}/kustomization.yaml",
        content=_render_kustomize_index([
            "namespace.yaml",
        ]),
        message=f"feat: initial kustomize index for {namespace}",
    )

    logger.info("Bootstrapped GitOps structure for user %s (namespace %s)", username, namespace)


def deploy_user_yaml(
    namespace: str,
    username: str,
    deployment_id: str,
    yaml_content: str,
    filename: str,
) -> str:
    """
    Commit a user's YAML manifest to the GitOps repo.
    FluxCD will detect the change within ~60 s and apply it.

    Returns the git path of the committed file.
    """
    # Validate it's valid YAML before committing
    try:
        docs = list(yaml.safe_load_all(yaml_content))
        if not docs or docs[0] is None:
            raise ValueError("Empty or invalid YAML")
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML: {e}") from e

    # Force the namespace field on every resource so users
    # cannot accidentally deploy into other namespaces
    patched_docs = [_patch_namespace(doc, namespace) for doc in docs if doc]
    patched_content = "---\n".join(yaml.dump(d, default_flow_style=False) for d in patched_docs)

    git_path = f"clusters/production/users/{namespace}/{filename}"

    _upsert_file(
        path=git_path,
        content=patched_content,
        message=f"deploy({namespace}): {filename} [{deployment_id[:8]}]",
    )

    # Update kustomization.yaml to include the new file
    _add_resource_to_kustomize_index(namespace, filename)

    logger.info("Committed %s for namespace %s", git_path, namespace)
    return git_path


def delete_user_deployment(namespace: str, filename: str):
    """Remove a deployment manifest from the GitOps repo."""
    git_path = f"clusters/production/users/{namespace}/{filename}"
    try:
        file_obj = _repo.get_contents(git_path, ref=GITHUB_BRANCH)
        _repo.delete_file(
            path=git_path,
            message=f"chore({namespace}): remove {filename}",
            sha=file_obj.sha,
            branch=GITHUB_BRANCH,
        )
    except GithubException as e:
        if e.status != 404:
            raise
    _remove_resource_from_kustomize_index(namespace, filename)


# ── Private helpers ───────────────────────────────────────────

def _upsert_file(path: str, content: str, message: str):
    """Create or update a file in the GitHub repo."""
    encoded = content.encode("utf-8")
    try:
        existing = _repo.get_contents(path, ref=GITHUB_BRANCH)
        # Only update if content changed
        if base64.b64decode(existing.content) != encoded:
            _repo.update_file(
                path=path,
                message=message,
                content=content,
                sha=existing.sha,
                branch=GITHUB_BRANCH,
            )
    except GithubException as e:
        if e.status == 404:
            _repo.create_file(
                path=path,
                message=message,
                content=content,
                branch=GITHUB_BRANCH,
            )
        else:
            raise


def _add_resource_to_kustomize_index(namespace: str, filename: str):
    index_path = f"clusters/production/users/{namespace}/kustomization.yaml"
    try:
        existing = _repo.get_contents(index_path, ref=GITHUB_BRANCH)
        data = yaml.safe_load(base64.b64decode(existing.content))
    except GithubException:
        data = {"apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization", "resources": []}

    resources: list = data.get("resources", [])
    if filename not in resources:
        resources.append(filename)
        data["resources"] = sorted(resources)
        _upsert_file(
            path=index_path,
            content=yaml.dump(data, default_flow_style=False),
            message=f"chore({namespace}): add {filename} to kustomization",
        )


def _remove_resource_from_kustomize_index(namespace: str, filename: str):
    index_path = f"clusters/production/users/{namespace}/kustomization.yaml"
    try:
        existing = _repo.get_contents(index_path, ref=GITHUB_BRANCH)
        data = yaml.safe_load(base64.b64decode(existing.content))
        resources: list = data.get("resources", [])
        if filename in resources:
            resources.remove(filename)
            data["resources"] = resources
            _upsert_file(
                path=index_path,
                content=yaml.dump(data, default_flow_style=False),
                message=f"chore({namespace}): remove {filename} from kustomization",
            )
    except GithubException:
        pass


def _patch_namespace(doc: dict, namespace: str) -> dict:
    """Force .metadata.namespace on a manifest."""
    if isinstance(doc, dict):
        meta = doc.setdefault("metadata", {})
        # Don't force namespace on cluster-scoped resources
        cluster_scoped = {"Namespace", "ClusterRole", "ClusterRoleBinding",
                          "PersistentVolume", "StorageClass"}
        if doc.get("kind") not in cluster_scoped:
            meta["namespace"] = namespace
    return doc


def _render_namespace_manifest(namespace: str, user_id: str, username: str) -> str:
    return yaml.dump({
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": namespace,
            "labels": {
                "app.kubernetes.io/managed-by": "deployment-platform",
                "platform/user-id": user_id,
                "platform/username": username,
            }
        }
    }, default_flow_style=False)


def _render_flux_kustomization(namespace: str) -> str:
    """
    FluxCD Kustomization CR — tells Flux to watch the user's directory
    in the GitOps repo and reconcile it into the cluster.
    """
    return yaml.dump({
        "apiVersion": "kustomize.toolkit.fluxcd.io/v1",
        "kind": "Kustomization",
        "metadata": {
            "name": f"user-{namespace}",
            "namespace": "flux-system",
        },
        "spec": {
            "interval": "1m0s",
            "path": f"./clusters/production/users/{namespace}",
            "prune": True,        # delete resources removed from git
            "sourceRef": {
                "kind": "GitRepository",
                "name": "flux-system",
            },
            "targetNamespace": namespace,
            "timeout": "5m0s",
            "wait": True,
        }
    }, default_flow_style=False)


def _render_kustomize_index(resources: list[str]) -> str:
    return yaml.dump({
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
        "resources": resources,
    }, default_flow_style=False)