# Architecture and RBAC model

## Data flow

1. Python runs on the Windows 11 workstation within a per-project `.venv`.
2. The official Kubernetes Python SDK loads a dedicated kubeconfig from the Windows user profile.
3. Requests use HTTPS to the kube-apiserver of an existing VMware-hosted kubeadm cluster.
4. Kubernetes ServiceAccount credentials authenticate requests; Kubernetes RBAC authorizes read operations.
5. The observer reads Kubernetes objects and, when permitted, Metrics API and platform-specific custom resources.
6. `k8s_report.py` normalizes observations and writes JSON/CSV snapshots **only to the local filesystem**.

## Required Kubernetes API permissions

| API group | Resources | Verbs | Needed for |
| --- | --- | --- | --- |
| Core (`""`) | `nodes` | `get`, `list` | Node inventory and capacity |
| Core (`""`) | `pods`, `namespaces` | `get`, `list` | Cluster inventory and status |

## Optional Kubernetes API permissions

| API group | Resources | Verbs | Needed for |
| --- | --- | --- | --- |
| `metrics.k8s.io` | `nodes`, `pods` | `get`, `list` | CPU/memory samples |
| `apps` | `daemonsets`, `deployments`, `statefulsets` | `get`, `list` | Cilium + Argo CD controller readiness |
| `argoproj.io` | `applications` | `get`, `list` | Argo CD sync and health |
| `cilium.io` | `ciliumnodes`, `ciliumendpoints` | `get`, `list` | Cilium node coverage and endpoint state |

The individual scripts require the subset of permissions associated with their checks. The consolidated reporter degrades optional checks to an explicit unavailable status when the API denies or cannot serve them.

## Data protection

- **Do not** substitute `/etc/kubernetes/admin.conf` for the read-only observer kubeconfig.
- Keep the kubeconfig outside the repository, at `%USERPROFILE%\.kube\k8s-observer-ro.yaml`.
- Never make GitHub Actions connect to the private lab or store tokens in the repo.
- Reports contain cluster names, Pod names, status, and topology; they are **not committed**.
- The observed Cilium/Argo CD state is informational; the observer does not issue rollout, restart, or mutation requests.

## Operational boundaries

The current collector is a one-shot snapshot tool. It does not run a controller reconciliation loop, actively test pod-to-pod connectivity, collect long-term metrics, query container logs, or provide alerting. Such capabilities would require additional design, permissions and tests.
