"""Read-only Kubernetes snapshot exporter (JSON + CSV)."""

import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from kubernetes.utils.quantity import parse_quantity

KUBECONFIG = Path.home() / ".kube" / "k8s-observer-ro.yaml"
NODE_FIELDS = [
    "name", "ready", "version", "cpu_capacity_cores", "cpu_allocatable_cores",
    "memory_capacity_mib", "memory_allocatable_mib", "cpu_used_mcores", "memory_used_mib",
]
POD_FIELDS = [
    "namespace", "name", "node", "phase", "ready", "health", "restarts",
    "reason", "cpu_used_mcores", "memory_used_mib",
]
NAMESPACE_FIELDS = [
    "namespace", "total", "healthy", "not_ready", "pending", "failed",
    "completed", "other", "restarts",
]
PLATFORM_FIELDS = ["component", "namespace", "name", "status", "details"]


def quantity(value, unit):
    """Convert a Kubernetes quantity into the requested unit."""
    raw = parse_quantity(value)
    if unit == "mcores":
        return round(float(raw * 1000), 2)
    if unit == "mib":
        return round(float(raw / (1024 ** 2)), 2)
    if unit == "cores":
        return round(float(raw), 3)
    raise ValueError(f"Unsupported unit: {unit}")


def is_ready(pod):
    return any(
        condition.type == "Ready" and condition.status == "True"
        for condition in (pod.status.conditions or [])
    )


def node_row(node):
    ready = next(
        (c.status for c in (node.status.conditions or []) if c.type == "Ready"),
        "Unknown",
    )
    return {
        "name": node.metadata.name,
        "ready": ready,
        "version": node.status.node_info.kubelet_version,
        "cpu_capacity_cores": quantity(node.status.capacity["cpu"], "cores"),
        "cpu_allocatable_cores": quantity(node.status.allocatable["cpu"], "cores"),
        "memory_capacity_mib": quantity(node.status.capacity["memory"], "mib"),
        "memory_allocatable_mib": quantity(node.status.allocatable["memory"], "mib"),
        "cpu_used_mcores": None,
        "memory_used_mib": None,
    }


def pod_row(pod):
    phase = pod.status.phase or "Unknown"
    ready = is_ready(pod)
    statuses = (
        list(pod.status.init_container_statuses or [])
        + list(pod.status.container_statuses or [])
        + list(pod.status.ephemeral_container_statuses or [])
    )
    reasons = [
        s.state.waiting.reason
        for s in statuses
        if s.state and s.state.waiting and s.state.waiting.reason
        and s.state.waiting.reason not in ("ContainerCreating", "PodInitializing")
    ]
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
    return {
        "namespace": pod.metadata.namespace,
        "name": pod.metadata.name,
        "node": pod.spec.node_name or "Unscheduled",
        "phase": phase,
        "ready": ready,
        "health": health,
        "restarts": sum(s.restart_count or 0 for s in statuses),
        "reason": reasons[0] if reasons else (pod.status.reason or ""),
        "cpu_used_mcores": None,
        "memory_used_mib": None,
    }


def namespace_rows(namespaces, pod_rows):
    stats = {ns.metadata.name: Counter() for ns in namespaces}
    for pod in pod_rows:
        c = stats.setdefault(pod["namespace"], Counter())
        c["total"] += 1
        c[pod["health"]] += 1
        c["restarts"] += pod["restarts"]
    result = []
    for name, c in sorted(stats.items()):
        known = sum(c[key] for key in (
            "Healthy", "NotReady", "Pending", "Failed", "Completed"
        ))
        result.append({
            "namespace": name, "total": c["total"],
            "healthy": c["Healthy"], "not_ready": c["NotReady"],
            "pending": c["Pending"], "failed": c["Failed"],
            "completed": c["Completed"], "other": c["total"] - known,
            "restarts": c["restarts"],
        })
    return result


def merge_metrics(node_rows, pod_rows, node_metrics, pod_metrics):
    nodes_by_name = {n["name"]: n for n in node_rows}
    pods_by_key = {(p["namespace"], p["name"]): p for p in pod_rows}
    for metric in node_metrics:
        row = nodes_by_name.get(metric["metadata"]["name"])
        if row:
            row["cpu_used_mcores"] = quantity(metric["usage"]["cpu"], "mcores")
            row["memory_used_mib"] = quantity(metric["usage"]["memory"], "mib")
    for metric in pod_metrics:
        meta = metric["metadata"]
        row = pods_by_key.get((meta["namespace"], meta["name"]))
        if row:
            row["cpu_used_mcores"] = round(sum(
                quantity(c["usage"]["cpu"], "mcores")
                for c in metric.get("containers", [])
            ), 2)
            row["memory_used_mib"] = round(sum(
                quantity(c["usage"]["memory"], "mib")
                for c in metric.get("containers", [])
            ), 2)


def write_csv(path, rows, fieldnames):
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_reports(base_path, snapshot):
    """Write machine-readable reports; do not serialize auth credentials."""
    base_path.mkdir(parents=True, exist_ok=True)
    json_path = base_path / "snapshot.json"
    with json_path.open("w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    for key, columns in (
        ("nodes", NODE_FIELDS), ("pods", POD_FIELDS),
        ("namespaces", NAMESPACE_FIELDS), ("platform", PLATFORM_FIELDS),
    ):
        write_csv(base_path / f"{key}.csv", snapshot[key], columns)
    return json_path


def optional_call(label, callback, availability):
    """Optional endpoint failures produce explicit incomplete-report status."""
    try:
        result = callback()
        availability[label] = "ok"
        return result
    except ApiException as exc:
        if exc.status == 401:
            raise
        availability[label] = f"unavailable (HTTP {exc.status})"
        return None


def take_snapshot(core, metrics, apps, custom):
    availability = {}
    nodes = sorted((node_row(n) for n in core.list_node().items),
                   key=lambda n: n["name"])
    pods = sorted((pod_row(p) for p in core.list_pod_for_all_namespaces().items),
                  key=lambda p: (p["namespace"], p["name"]))
    namespaces = namespace_rows(core.list_namespace().items, pods)

    node_metrics = optional_call(
        "node_metrics", lambda: metrics.list_cluster_custom_object(
            "metrics.k8s.io", "v1beta1", "nodes"
        )["items"], availability,
    )
    pod_metrics = optional_call(
        "pod_metrics", lambda: metrics.list_cluster_custom_object(
            "metrics.k8s.io", "v1beta1", "pods"
        )["items"], availability,
    )
    merge_metrics(nodes, pods, node_metrics or [], pod_metrics or [])

    platform = []
    daemonsets = optional_call(
        "cilium_daemonset", lambda: apps.read_namespaced_daemon_set(
            "cilium", "kube-system"
        ), availability,
    )
    if daemonsets:
        platform.append({
            "component": "Cilium DaemonSet", "namespace": "kube-system",
            "name": "cilium", "status": (
                "Ready" if (daemonsets.status.desired_number_scheduled or 0) > 0
                and (daemonsets.status.number_ready or 0) ==
                (daemonsets.status.desired_number_scheduled or 0) else "NotReady"
            ),
            "details": f"{daemonsets.status.number_ready or 0}/"
                       f"{daemonsets.status.desired_number_scheduled or 0} ready",
        })
    # Check the Cilium operator and Argo CD controller workloads.
    operator = optional_call(
        "cilium_operator", lambda: apps.read_namespaced_deployment(
            "cilium-operator", "kube-system"
        ), availability,
    )
    if operator:
        desired = operator.spec.replicas or 0
        ready = operator.status.ready_replicas or 0
        platform.append({
            "component": "Cilium Operator", "namespace": "kube-system",
            "name": "cilium-operator", "status":
            "Ready" if desired > 0 and ready == desired else "NotReady",
            "details": f"{ready}/{desired} ready",
        })
    argo_deployments = optional_call(
        "argocd_deployments", lambda: apps.list_deployment_for_all_namespaces().items,
        availability,
    )
    argo_statefulsets = optional_call(
        "argocd_statefulsets", lambda: apps.list_stateful_set_for_all_namespaces().items,
        availability,
    )
    for kind, workloads in (("Deployment", argo_deployments),
                            ("StatefulSet", argo_statefulsets)):
        for obj in workloads or []:
            labels = obj.metadata.labels or {}
            if not (obj.metadata.namespace == "argocd"
                    or obj.metadata.name.startswith("argocd-")
                    or labels.get("app.kubernetes.io/part-of") == "argocd"):
                continue
            desired = obj.spec.replicas or 0
            ready = obj.status.ready_replicas or 0
            platform.append({
                "component": f"Argo CD {kind}",
                "namespace": obj.metadata.namespace,
                "name": obj.metadata.name,
                "status": "Ready" if desired > 0 and ready == desired else "NotReady",
                "details": f"{ready}/{desired} ready",
            })
    # Compare CiliumNode registrations with Kubernetes node inventory.
    cilium_nodes = optional_call(
        "cilium_nodes", lambda: custom.list_cluster_custom_object(
            "cilium.io", "v2", "ciliumnodes"
        )["items"], availability,
    )
    if cilium_nodes is not None:
        names = {n["metadata"]["name"] for n in cilium_nodes}
        missing = sorted({n["name"] for n in nodes} - names)
        platform.append({
            "component": "Cilium Nodes", "namespace": "cluster",
            "name": "registration", "status": "Ready" if not missing else "Missing",
            "details": f"{len(names)}/{len(nodes)} registered; missing={','.join(missing) or 'none'}",
        })
    argo_apps = optional_call(
        "argocd_applications", lambda: custom.list_cluster_custom_object(
            "argoproj.io", "v1alpha1", "applications"
        )["items"], availability,
    )
    for app in argo_apps or []:
        st = app.get("status") or {}
        sync = (st.get("sync") or {}).get("status", "Unknown")
        health = (st.get("health") or {}).get("status", "Unknown")
        platform.append({
            "component": "Argo CD Application",
            "namespace": app["metadata"].get("namespace", ""),
            "name": app["metadata"]["name"], "status": health,
            "details": f"Sync={sync}; Health={health}",
        })
    endpoints = optional_call(
        "cilium_endpoints", lambda: custom.list_cluster_custom_object(
            "cilium.io", "v2", "ciliumendpoints"
        )["items"], availability,
    )
    counts = Counter((ep.get("status") or {}).get("state", "Unknown")
                     for ep in endpoints or [])
    if endpoints is not None:
        platform.append({
            "component": "Cilium Endpoints", "namespace": "all",
            "name": "summary", "status": "Observed",
            "details": json.dumps(dict(sorted(counts.items())), sort_keys=True),
        })
    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "availability": availability,
        "nodes": nodes, "pods": pods, "namespaces": namespaces,
        "platform": sorted(platform, key=lambda x: (x["component"], x["name"])),
    }


def main():
    # Never print or export kubeconfig/token content
    config.load_kube_config(config_file=str(KUBECONFIG))
    try:
        snapshot = take_snapshot(
            client.CoreV1Api(), client.CustomObjectsApi(),
            client.AppsV1Api(), client.CustomObjectsApi(),
        )
    except ApiException as exc:
        if exc.status == 401:
            raise SystemExit("401: expired/invalid credentials; renew the token.")
        if exc.status == 403:
            raise SystemExit("403: required node/pod/namespace RBAC is missing.")
        raise
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    path = Path("reports") / name
    json_path = write_reports(path, snapshot)
    print(f"Report: {json_path.resolve()}")
    print(f"Nodes={len(snapshot['nodes'])}, Pods={len(snapshot['pods'])}, "
          f"Namespaces={len(snapshot['namespaces'])}, "
          f"Platform records={len(snapshot['platform'])}")
    for section, status in snapshot["availability"].items():
        print(f"{section}: {status}")
    if any(status != "ok" for status in snapshot["availability"].values()):
        print("WARNING: Report is incomplete; see 'availability' in snapshot.json")


if __name__ == "__main__":
    main()
