"""Unit tests that run without a cluster, kubeconfig, or internet."""

import csv
import json
from types import SimpleNamespace as NS

import pytest

from k8s_report import (
    merge_metrics, namespace_rows, node_row, pod_row,
    quantity, write_reports,
)


def node_fixture():
    return NS(
        metadata=NS(name="k8s-w1"),
        status=NS(
            conditions=[NS(type="Ready", status="True")],
            node_info=NS(kubelet_version="v1.36.5"),
            capacity={"cpu": "8", "memory": "3Gi"},
            allocatable={"cpu": "7800m", "memory": "2800Mi"},
        ),
    )


def pod_fixture(phase="Running", ready=True, restarts=2):
    return NS(
        metadata=NS(name="demo", namespace="default", deletion_timestamp=None),
        spec=NS(node_name="k8s-w1"),
        status=NS(
            phase=phase, reason=None,
            conditions=[NS(type="Ready", status="True" if ready else "False")],
            init_container_statuses=[], ephemeral_container_statuses=[],
            container_statuses=[NS(restart_count=restarts, state=NS(waiting=None))],
        ),
    )


def test_resource_units():
    assert quantity("750m", "mcores") == 750
    assert quantity("3Gi", "mib") == 3072
    assert quantity("7800m", "cores") == 7.8


def test_node_capacity_and_allocatable():
    row = node_row(node_fixture())
    assert row["ready"] == "True"
    assert row["cpu_capacity_cores"] == 8
    assert row["cpu_allocatable_cores"] == 7.8
    assert row["memory_capacity_mib"] == 3072
    assert row["memory_allocatable_mib"] == 2800


def test_running_not_ready_is_not_healthy():
    row = pod_row(pod_fixture(ready=False))
    assert row["health"] == "NotReady"
    assert row["restarts"] == 2


def test_completed_pod_is_not_a_failure():
    assert pod_row(pod_fixture(phase="Succeeded", ready=False))["health"] == "Completed"


def test_empty_namespace_is_retained():
    names = [NS(metadata=NS(name="default")), NS(metadata=NS(name="empty"))]
    rows = namespace_rows(names, [pod_row(pod_fixture())])
    assert next(r for r in rows if r["namespace"] == "empty")["total"] == 0
    assert next(r for r in rows if r["namespace"] == "default")["restarts"] == 2


def test_metrics_merge():
    nodes = [node_row(node_fixture())]
    pods = [pod_row(pod_fixture())]
    merge_metrics(
        nodes, pods,
        [{"metadata": {"name": "k8s-w1"}, "usage": {"cpu": "250m", "memory": "512Mi"}}],
        [{"metadata": {"name": "demo", "namespace": "default"},
          "containers": [{"usage": {"cpu": "120m", "memory": "64Mi"}}]}],
    )
    assert nodes[0]["cpu_used_mcores"] == 250
    assert pods[0]["memory_used_mib"] == 64


def test_json_csv_exports(tmp_path):
    report = {
        "schema_version": 1,
        "generated_at_utc": "2026-11-16T00:00:00+00:00",
        "availability": {},
        "nodes": [node_row(node_fixture())],
        "pods": [pod_row(pod_fixture())],
        "namespaces": namespace_rows([NS(metadata=NS(name="default"))], [pod_row(pod_fixture())]),
        "platform": [],
    }
    path = write_reports(tmp_path, report)
    assert json.loads(path.read_text(encoding="utf-8"))["nodes"][0]["name"] == "k8s-w1"
    with (tmp_path / "pods.csv").open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["namespace"] == "default"
    assert (tmp_path / "platform.csv").exists()
    assert "token" not in path.read_text(encoding="utf-8").lower()
