from pathlib import Path
from collections import Counter
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

# Use existing least-privilege ServiceAccount credentials
KCFG = Path.home() / ".kube" / "k8s-observer-ro.yaml"
config.load_kube_config(config_file=str(KCFG))

core = client.CoreV1Api()
apps = client.AppsV1Api()
custom = client.CustomObjectsApi()

def pod_ready(pod):
    return any(
        c.type == "Ready" and c.status == "True"
        for c in (pod.status.conditions or [])
    )

def deployment_status(obj):
    desired = obj.spec.replicas
    ready = obj.status.ready_replicas or 0
    available = obj.status.available_replicas or 0
    return f"Ready={ready}/{desired} Available={available}"

def read_cilium_objects(plural):
    try:
        return custom.list_cluster_custom_object(
            group="cilium.io", version="v2", plural=plural
        )["items"]
    except ApiException as exc:
        if exc.status == 404:
            print(f"{plural}: API resource unavailable")
            return []
        raise

def observe():
    print("\nCILIUM + ARGO CD PLATFORM OBSERVER")
    print("=" * 75)

    # Check Cilium agent DaemonSet coverage
    print("\n[1] CILIUM AGENT DAEMONSET")
    ds = apps.read_namespaced_daemon_set(
        name="cilium", namespace="kube-system"
    )

    desired = ds.status.desired_number_scheduled or 0
    ready = ds.status.number_ready or 0
    updated = ds.status.updated_number_scheduled or 0

    print(
        f"Cilium: Desired={desired} "
        f"Ready={ready} Updated={updated}"
    )

    # Check the Cilium operator Deployment
    print("\n[2] CILIUM OPERATOR")
    operator = apps.read_namespaced_deployment(
        name="cilium-operator", namespace="kube-system"
    )
    print(deployment_status(operator))

    # Inspect Cilium agents and their container restart histories
    print("\n[3] CILIUM AGENT PODS")
    system_pods = core.list_namespaced_pod(
        namespace="kube-system"
    ).items

    agents = [
        pod for pod in system_pods
        if (pod.metadata.labels or {}).get("k8s-app") == "cilium"
    ]

    for pod in sorted(agents, key=lambda p: p.spec.node_name or ""):
        restarts = sum(
            c.restart_count or 0
            for c in (pod.status.container_statuses or [])
        )
        print(
            f"{pod.spec.node_name}: "
            f"Ready={pod_ready(pod)} Restarts={restarts}"
        )

    if not agents:
        print("No agents matched the standard Cilium label.")

    # Inspect CiliumNode registration against Kubernetes nodes
    print("\n[4] CILIUM NODE REGISTRATION")
    cilium_nodes = read_cilium_objects("ciliumnodes")
    k8s_nodes = {
        n.metadata.name for n in core.list_node().items
    }
    registered = {
        n["metadata"]["name"] for n in cilium_nodes
    }

    print(f"Kubernetes nodes: {len(k8s_nodes)}")
    print(f"CiliumNodes:      {len(registered)}")

    for name in sorted(k8s_nodes - registered):
        print(f"Missing CiliumNode: {name}")

    # Inspect the lifecycle state of Cilium endpoints
    print("\n[5] CILIUM ENDPOINT HEALTH")
    endpoints = read_cilium_objects("ciliumendpoints")

    states = Counter(
        ep.get("status", {}).get("state", "Unknown")
        for ep in endpoints
    )

    for state, count in sorted(states.items()):
        print(f"{state}: {count}")

    for ep in endpoints:
        state = ep.get("status", {}).get("state", "Unknown")
        if state != "ready":
            meta = ep["metadata"]
            print(
                f"Review endpoint: "
                f"{meta.get('namespace')}/{meta['name']} "
                f"State={state}"
            )

    # Discover Argo CD Deployments and StatefulSets
    print("\n[6] ARGO CD CONTROLLER WORKLOADS")

    def is_argocd(obj):
        labels = obj.metadata.labels or {}
        return (
            obj.metadata.namespace == "argocd"
            or obj.metadata.name.startswith("argocd-")
            or labels.get("app.kubernetes.io/part-of") == "argocd"
        )

    deployments = apps.list_deployment_for_all_namespaces().items
    statefulsets = apps.list_stateful_set_for_all_namespaces().items

    components = [
        ("Deployment", obj)
        for obj in deployments if is_argocd(obj)
    ] + [
        ("StatefulSet", obj)
        for obj in statefulsets if is_argocd(obj)
    ]

    for kind, obj in sorted(
        components, key=lambda x: x[1].metadata.name
    ):
        print(
            f"{kind}: {obj.metadata.namespace}/"
            f"{obj.metadata.name} "
            f"{deployment_status(obj)}"
        )

    if not components:
        print("No matching Argo CD controller workloads found.")

    # Read Argo CD Application CRD reconciliation status
    print("\n[7] ARGO CD APPLICATION SYNC / HEALTH")
    applications = custom.list_cluster_custom_object(
        group="argoproj.io",
        version="v1alpha1",
        plural="applications"
    )["items"]

    for app in sorted(
        applications, key=lambda a: a["metadata"]["name"]
    ):
        name = app["metadata"]["name"]
        status = app.get("status") or {}
        sync = (status.get("sync") or {}).get("status", "Unknown")
        health = (status.get("health") or {}).get("status", "Unknown")

        print(f"{name:<32} Sync={sync:<12} Health={health}")

    print("\nPlatform inventory completed.")

try:
    observe()
except ApiException as exc:
    if exc.status == 401:
        raise SystemExit("Authentication failure: renew the token.")
    if exc.status == 403:
        raise SystemExit("Forbidden: verify observer RBAC permissions.")
    if exc.status == 404:
        raise SystemExit(
            "A required Kubernetes resource was not found. "
            "Check Cilium/Argo CD component names and namespaces."
        )
    raise
