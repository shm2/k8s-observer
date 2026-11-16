from pathlib import Path
from decimal import Decimal
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from kubernetes.utils.quantity import parse_quantity

# Connect with our existing restricted kubeconfig
KCFG = Path.home() / ".kube" / "k8s-observer-ro.yaml"
config.load_kube_config(config_file=str(KCFG))

core = client.CoreV1Api()
metrics = client.CustomObjectsApi()

# Convert Kubernetes quantities to human-readable units
def mcpu(value):
    return parse_quantity(value) * Decimal(1000)

def mib(value):
    return parse_quantity(value) / Decimal(1024 ** 2)

def percent(used, available):
    return used / available * 100 if available else Decimal(0)

try:
    # Read node capacity and allocatable resources
    nodes = {
        n.metadata.name: n
        for n in core.list_node().items
    }

    # Query live metrics through the aggregated Metrics API
    node_metrics = metrics.list_cluster_custom_object(
        group="metrics.k8s.io",
        version="v1beta1",
        plural="nodes"
    )["items"]

    pod_metrics = metrics.list_cluster_custom_object(
        group="metrics.k8s.io",
        version="v1beta1",
        plural="pods"
    )["items"]

except ApiException as exc:
    if exc.status == 401:
        raise SystemExit("Authentication failed: renew your token.")
    if exc.status == 403:
        raise SystemExit("Forbidden: check Phase 4 RBAC permissions.")
    if exc.status == 503:
        raise SystemExit("Metrics API unavailable: check Metrics Server.")
    raise

# Report node CPU and memory usage as a percentage of allocatable
print("\nPHASE 4 — NODE RESOURCE UTILIZATION")
print("=" * 82)
print(
    f"{'NODE':<12} {'CPU(m)':>10} {'CPU %':>10} "
    f"{'MEM(MiB)':>13} {'MEM %':>10}"
)
print("-" * 82)

for metric in sorted(node_metrics, key=lambda m: m["metadata"]["name"]):
    name = metric["metadata"]["name"]
    node = nodes.get(name)

    if node is None:
        continue

    cpu_used = mcpu(metric["usage"]["cpu"])
    mem_used = mib(metric["usage"]["memory"])

    cpu_alloc = mcpu(node.status.allocatable["cpu"])
    mem_alloc = mib(node.status.allocatable["memory"])

    print(
        f"{name:<12} {cpu_used:>10.1f} "
        f"{percent(cpu_used, cpu_alloc):>9.1f}% "
        f"{mem_used:>13.1f} "
        f"{percent(mem_used, mem_alloc):>9.1f}%"
    )
    print(
        f"  Sample: {metric.get('timestamp', '?')} "
        f"| Window: {metric.get('window', '?')}"
    )

# Aggregate the metrics for all containers in each Pod
pods = []

for metric in pod_metrics:
    containers = metric.get("containers", [])

    cpu_used = sum(
        (mcpu(c["usage"]["cpu"]) for c in containers),
        Decimal(0)
    )
    mem_used = sum(
        (mib(c["usage"]["memory"]) for c in containers),
        Decimal(0)
    )

    pods.append({
        "namespace": metric["metadata"]["namespace"],
        "name": metric["metadata"]["name"],
        "cpu": cpu_used,
        "memory": mem_used,
    })

# Show top consumers without modifying the cluster
def show_top(title, key):
    print(f"\n{title}")
    print("-" * 82)
    print(f"{'NAMESPACE/POD':<65} {'CPU(m)':>8} {'MEM(MiB)':>9}")

    for pod in sorted(pods, key=lambda p: p[key], reverse=True)[:10]:
        identity = f"{pod['namespace']}/{pod['name']}"
        print(
            f"{identity:<65} "
            f"{pod['cpu']:>8.1f} "
            f"{pod['memory']:>9.1f}"
        )

show_top("TOP 10 PODS BY CPU", "cpu")
show_top("TOP 10 PODS BY MEMORY", "memory")

print("\nMetrics Server inventory completed.")
