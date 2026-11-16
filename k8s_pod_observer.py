from pathlib import Path
from collections import Counter
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
import sys

# Load existing read-only Kubernetes credentials
KUBECONFIG = Path.home() / ".kube" / "k8s-observer-ro.yaml"
config.load_kube_config(config_file=str(KUBECONFIG))

# Connect to the Kubernetes CoreV1 API
v1 = client.CoreV1Api()

try:
    # List every namespace and every Pod in the cluster
    namespaces = v1.list_namespace().items
    pods = v1.list_pod_for_all_namespaces().items
except ApiException as error:
    # Distinguish expired credentials from missing RBAC access
    if error.status == 401:
        sys.exit("Authentication failed: check token expiration.")
    if error.status == 403:
        sys.exit("Access forbidden: check Phase 3 RBAC permissions.")
    raise

# Initialize namespace counters, including empty namespaces
summary = {
    ns.metadata.name: Counter()
    for ns in namespaces
}

records = []

for pod in pods:
    namespace = pod.metadata.namespace
    name = pod.metadata.name
    node = pod.spec.node_name or "Unscheduled"
    phase = pod.status.phase or "Unknown"

    # Determine Pod readiness using the Ready condition
    ready = any(
        condition.type == "Ready" and condition.status == "True"
        for condition in (pod.status.conditions or [])
    )

    # Include regular, init and ephemeral container statuses
    statuses = (
        list(pod.status.container_statuses or [])
        + list(pod.status.init_container_statuses or [])
        + list(pod.status.ephemeral_container_statuses or [])
    )

    # Sum cumulative container restarts for this Pod
    restarts = sum(
        status.restart_count or 0
        for status in statuses
    )

    # Inspect container waiting reasons for diagnostic detail
    waiting_reasons = [
        status.state.waiting.reason
        for status in statuses
        if status.state
        and status.state.waiting
        and status.state.waiting.reason
        and status.state.waiting.reason not in (
            "ContainerCreating", "PodInitializing"
        )
    ]

    reason = (
        waiting_reasons[0] if waiting_reasons
        else pod.status.reason or "-"
    )

    # Classify Pod health beyond its basic lifecycle phase
    if pod.metadata.deletion_timestamp:
        health = "Terminating"
    elif phase == "Succeeded":
        health = "Completed"
    elif phase == "Running" and ready:
        health = "Healthy"
    elif phase == "Running":
        health = "NotReady"
    else:
        health = phase

    # Build per-namespace summary counters
    counts = summary.setdefault(namespace, Counter())
    counts["Total"] += 1
    counts[health] += 1
    counts["Restarts"] += restarts

    # Store detailed Pod information
    records.append({
        "namespace": namespace,
        "name": name,
        "node": node,
        "health": health,
        "restarts": restarts,
        "reason": reason,
    })

# Display cluster-wide inventory totals
print("\nKUBERNETES POD OBSERVER — PHASE 3")
print("=" * 96)
print(f"Namespaces: {len(namespaces)}")
print(f"Pods:       {len(pods)}")
print(f"Restarts:   {sum(r['restarts'] for r in records)}")

# Display one summary row per namespace
print("\nNAMESPACE INVENTORY")
print("-" * 96)
print(
    f"{'NAMESPACE':<24} {'TOTAL':>5} {'HEALTHY':>8} "
    f"{'NOTREADY':>9} {'PENDING':>8} {'FAILED':>7} "
    f"{'DONE':>6} {'OTHER':>6} {'RESTARTS':>8}"
)

for namespace in sorted(summary):
    c = summary[namespace]

    known = sum(
        c[k] for k in (
            "Healthy", "NotReady", "Pending",
            "Failed", "Completed"
        )
    )
    other = c["Total"] - known

    print(
        f"{namespace:<24} {c['Total']:>5} "
        f"{c['Healthy']:>8} {c['NotReady']:>9} "
        f"{c['Pending']:>8} {c['Failed']:>7} "
        f"{c['Completed']:>6} {other:>6} "
        f"{c['Restarts']:>8}"
    )

# Show all Pods with --all; otherwise highlight potential issues
show_all = "--all" in sys.argv

selected = records if show_all else [
    r for r in records
    if r["health"] not in ("Healthy", "Completed")
    or r["restarts"] > 0
]

print("\nALL PODS" if show_all else "\nPODS FOR REVIEW / RESTART HISTORY")
print("-" * 96)

if not selected:
    print("No Pods matched the review criteria.")

for r in sorted(selected, key=lambda x: (x["namespace"], x["name"])):
    print(
        f"{r['namespace']}/{r['name']}\n"
        f"  Node={r['node']} | Health={r['health']} | "
        f"Restarts={r['restarts']} | Reason={r['reason']}"
    )

print("\nPhase 3 inventory complete.")
