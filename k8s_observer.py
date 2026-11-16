from pathlib import Path
from decimal import Decimal
from kubernetes import client, config
from kubernetes.utils.quantity import parse_quantity

# Load the dedicated read-only Kubernetes credentials
kubeconfig = Path.home() / ".kube" / "k8s-observer-ro.yaml"
config.load_kube_config(config_file=str(kubeconfig))

# Initialize the Kubernetes CoreV1 API client
v1 = client.CoreV1Api()

# Query all cluster nodes
nodes = sorted(v1.list_node().items, key=lambda n: n.metadata.name)

# Convert Kubernetes memory quantities to GiB
def to_gib(quantity):
    return parse_quantity(quantity) / Decimal(1024 ** 3)

print(f"\nDiscovered {len(nodes)} Kubernetes nodes")
print("=" * 70)

for node in nodes:
    # Read node identity and Kubernetes version
    name = node.metadata.name
    version = node.status.node_info.kubelet_version

    # Determine node readiness
    ready = next(
        (c.status for c in node.status.conditions if c.type == "Ready"),
        "Unknown"
    )

    # Retrieve reported CPU capacity and allocatable CPU
    cpu_capacity = parse_quantity(node.status.capacity["cpu"])
    cpu_allocatable = parse_quantity(node.status.allocatable["cpu"])

    # Retrieve memory capacity and allocatable memory
    mem_capacity = to_gib(node.status.capacity["memory"])
    mem_allocatable = to_gib(node.status.allocatable["memory"])

    # Calculate resources not allocatable to Pods
    cpu_difference = cpu_capacity - cpu_allocatable
    mem_difference = mem_capacity - mem_allocatable

    # Print the node's resource inventory
    print(f"\nNODE: {name}")
    print(f"Status: Ready={ready} | Kubernetes={version}")
    print("-" * 70)

    print("CPU (cores)")
    print(f"  Capacity:        {cpu_capacity:.2f}")
    print(f"  Allocatable:     {cpu_allocatable:.2f}")
    print(f"  Non-allocatable: {cpu_difference:.2f}")

    print("MEMORY (GiB)")
    print(f"  Capacity:        {mem_capacity:.2f}")
    print(f"  Allocatable:     {mem_allocatable:.2f}")
    print(f"  Non-allocatable: {mem_difference:.2f}")

print("\n" + "=" * 70)
print("Node resource inventory completed successfully.")
