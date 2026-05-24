"""
Kubernetes service layer.
Creates per-user namespaces with resource quotas, limit ranges,
and network policies so users are isolated from each other.
"""

import logging
import os
from kubernetes import client, config
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)


def _load_k8s_config():
    """Load in-cluster config when running in a pod, fall back to kubeconfig."""
    try:
        config.load_incluster_config()
        logger.info("Loaded in-cluster Kubernetes config")
    except config.ConfigException:
        config.load_kube_config()
        logger.info("Loaded kubeconfig from disk")


_load_k8s_config()

core_v1    = client.CoreV1Api()
rbac_v1    = client.RbacAuthorizationV1Api()
custom_api = client.CustomObjectsApi()
apps_v1    = client.AppsV1Api()


# ── Namespace creation ────────────────────────────────────────

def create_user_namespace(namespace: str, user_id: str, username: str) -> bool:
    """
    Create a Kubernetes namespace for a user.
    Idempotent — safe to call if namespace already exists.
    """
    ns_body = client.V1Namespace(
        metadata=client.V1ObjectMeta(
            name=namespace,
            labels={
                "app.kubernetes.io/managed-by": "deployment-platform",
                "platform/user-id":             user_id,
                "platform/username":            username,
            },
            annotations={
                "platform/created-by": "deployment-platform-api",
            }
        )
    )

    try:
        core_v1.create_namespace(ns_body)
        logger.info("Created namespace %s", namespace)
    except ApiException as e:
        if e.status == 409:
            logger.info("Namespace %s already exists", namespace)
        else:
            logger.error("Failed to create namespace %s: %s", namespace, e)
            raise

    _apply_resource_quota(namespace)
    _apply_limit_range(namespace)
    _apply_network_policy(namespace)

    return True


def _apply_resource_quota(namespace: str):
    """Prevent one user from consuming all cluster resources."""
    quota = client.V1ResourceQuota(
        metadata=client.V1ObjectMeta(name="user-quota", namespace=namespace),
        spec=client.V1ResourceQuotaSpec(
            hard={
                "requests.cpu":    "2",
                "requests.memory": "2Gi",
                "limits.cpu":      "4",
                "limits.memory":   "4Gi",
                "pods":            "20",
                "services":        "10",
            }
        )
    )
    try:
        core_v1.create_namespaced_resource_quota(namespace, quota)
    except ApiException as e:
        if e.status != 409:
            raise


def _apply_limit_range(namespace: str):
    """Set default container resource limits."""
    lr = client.V1LimitRange(
        metadata=client.V1ObjectMeta(name="user-limits", namespace=namespace),
        spec=client.V1LimitRangeSpec(
            limits=[client.V1LimitRangeItem(
                type="Container",
                default={"cpu": "200m", "memory": "256Mi"},
                default_request={"cpu": "100m", "memory": "128Mi"},
                max={"cpu": "2", "memory": "2Gi"},
            )]
        )
    )
    try:
        core_v1.create_namespaced_limit_range(namespace, lr)
    except ApiException as e:
        if e.status != 409:
            raise


def _apply_network_policy(namespace: str):
    """
    Deny all cross-namespace traffic by default.
    Users can only reach pods within their own namespace.
    """
    policy = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": "deny-cross-namespace", "namespace": namespace},
        "spec": {
            "podSelector": {},       # applies to all pods in namespace
            "policyTypes": ["Ingress", "Egress"],
            "ingress": [
                # Allow from same namespace
                {"from": [{"podSelector": {}}]},
                # Allow from ingress-nginx namespace
                {"from": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "ingress-nginx"}}}]},
            ],
            "egress": [
                # Allow to same namespace
                {"to": [{"podSelector": {}}]},
                # Allow DNS
                {"ports": [{"port": 53, "protocol": "UDP"}, {"port": 53, "protocol": "TCP"}]},
                # Allow internet egress (pull images, call external APIs)
                {"to": [{"ipBlock": {"cidr": "0.0.0.0/0", "except": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]}}]},
            ]
        }
    }

    try:
        custom_api.create_namespaced_custom_object(
            group="networking.k8s.io",
            version="v1",
            namespace=namespace,
            plural="networkpolicies",
            body=policy,
        )
    except ApiException as e:
        if e.status != 409:
            raise


# ── Status helpers ────────────────────────────────────────────

def get_namespace_pods(namespace: str) -> list[dict]:
    pods = core_v1.list_namespaced_pod(namespace)
    result = []
    for pod in pods.items:
        result.append({
            "name":     pod.metadata.name,
            "phase":    pod.status.phase,
            "ready":    _pod_ready(pod),
            "restarts": sum(
                cs.restart_count for cs in (pod.status.container_statuses or [])
            ),
            "node":     pod.spec.node_name,
        })
    return result


def get_flux_kustomization_status(namespace: str, name: str) -> dict:
    try:
        ks = custom_api.get_namespaced_custom_object(
            group="kustomize.toolkit.fluxcd.io",
            version="v1",
            namespace="flux-system",
            plural="kustomizations",
            name=name,
        )
        conditions = ks.get("status", {}).get("conditions", [])
        ready = next((c for c in conditions if c["type"] == "Ready"), {})
        return {
            "name":    name,
            "ready":   ready.get("status") == "True",
            "reason":  ready.get("reason", ""),
            "message": ready.get("message", ""),
        }
    except ApiException:
        return {"name": name, "ready": False, "reason": "NotFound", "message": ""}


def _pod_ready(pod) -> bool:
    if not pod.status.conditions:
        return False
    ready_cond = next((c for c in pod.status.conditions if c.type == "Ready"), None)
    return ready_cond is not None and ready_cond.status == "True"


def delete_user_namespace(namespace: str):
    try:
        core_v1.delete_namespace(namespace)
        logger.info("Deleted namespace %s", namespace)
    except ApiException as e:
        if e.status != 404:
            raise