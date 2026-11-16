# K8s Observer — Phase 5 reporting and testing

Read-only Kubernetes observability from a Windows 11 Python virtual environment into a three-node VMware/kubeadm cluster.

## Features

- Inventory node readiness, Kubernetes versions, capacity and allocatable resources.
- Inventory all namespaces and Pod readiness, phase, and cumulative restarts.
- Collect optional live CPU and memory usage from the Metrics API.
- Collect optional Cilium DaemonSet, endpoint state and Argo CD Application status.
- Export snapshot JSON and separate nodes/pods/namespaces/platform CSV files under `reports/`.
- Run offline tests (no cluster access required).

## Windows PowerShell commands

```powershell
# Install dependencies in an existing venv
.\.venv\Scripts\python.exe -m pip install kubernetes pytest

# Run all offline tests
.\.venv\Scripts\python.exe -m pytest -q

# Collect a live cluster snapshot (requires restricted kubeconfig)
.\.venv\Scripts\python.exe .\k8s_report.py
```

## Security

Authentication reads `%USERPROFILE%\.kube\k8s-observer-ro.yaml`, which is **not** committed.
The ServiceAccount must have read-only RBAC access to nodes, pods and namespaces; optional endpoints are included only if accessible.
Generated reports can reveal infrastructure topology and are excluded from Git by default.
Short-lived ServiceAccount tokens require renewal.

## Metrics limitations

Metrics Server reports near-real-time CPU and memory samples, not a historical metrics database. Empty metric values mean the sample was unavailable, not zero consumption. Pod readiness and Cilium/Argo conditions are point-in-time observations, not end-to-end guarantees.
