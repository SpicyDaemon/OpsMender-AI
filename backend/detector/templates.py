"""Built-in detector rule templates."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DetectorTemplate:
    key: str
    label: str
    description: str
    prompt_template: str
    severity_default: str = "medium"
    interval_seconds: int = 300


_TEMPLATES: tuple[DetectorTemplate, ...] = (
    DetectorTemplate(
        key="k8s_crashloop",
        label="Kubernetes CrashLoopBackOff / OOMKilled",
        description="Look for pods repeatedly restarting, crashlooping, or getting OOM-killed.",
        prompt_template=(
            "Inspect the Kubernetes cluster for unhealthy workloads. Focus on pods "
            "in CrashLoopBackOff, OOMKilled containers, and workloads with repeated "
            "recent restarts. If you find an issue, summarize the namespace, workload, "
            "failing pod names, and the strongest evidence."
        ),
        severity_default="high",
        interval_seconds=300,
    ),
    DetectorTemplate(
        key="k8s_high_restarts",
        label="Kubernetes High Restart Count",
        description="Detect workloads with abnormal restart counts even if they are currently running.",
        prompt_template=(
            "Inspect the Kubernetes cluster for pods or deployments with unusually "
            "high restart counts compared to normal operation. Prefer issues that "
            "suggest instability, flapping, or hidden crashes even when the current "
            "status still appears Running."
        ),
        severity_default="medium",
        interval_seconds=600,
    ),
    DetectorTemplate(
        key="k8s_http_5xx",
        label="Kubernetes Elevated 5xx Errors",
        description="Check for strong signs of elevated server-side HTTP failures in logs, metrics, or events.",
        prompt_template=(
            "Inspect available cluster signals for elevated 5xx errors, backend "
            "request failures, or failing services. Use logs, metrics-like tools, "
            "or events if available. Only report an incident when the evidence "
            "suggests a meaningful ongoing production impact."
        ),
        severity_default="high",
        interval_seconds=300,
    ),
    DetectorTemplate(
        key="k8s_node_notready",
        label="Kubernetes Node NotReady",
        description="Detect cluster nodes that are NotReady, unreachable, or otherwise unhealthy.",
        prompt_template=(
            "Inspect the Kubernetes cluster for nodes that are NotReady, "
            "unschedulable due to health issues, or showing strong signs of "
            "degradation. Include affected nodes and downstream workload impact "
            "if visible."
        ),
        severity_default="high",
        interval_seconds=300,
    ),
    DetectorTemplate(
        key="k8s_pvc_capacity",
        label="Kubernetes PVC Near Capacity",
        description="Look for persistent volumes or claims nearing capacity or causing storage pressure.",
        prompt_template=(
            "Inspect the Kubernetes cluster for persistent volumes or claims that "
            "appear near capacity, under storage pressure, or actively impacting "
            "workloads. Include affected namespaces, claims, and visible symptoms."
        ),
        severity_default="medium",
        interval_seconds=900,
    ),
    DetectorTemplate(
        key="generic_unusual_activity",
        label="Generic Unusual Activity",
        description="Catch-all prompt for any MCP server: summarize anything clearly abnormal in recent signals.",
        prompt_template=(
            "Inspect the MCP server for anything clearly unusual in the most recent "
            "operational state. Focus on errors, unhealthy resources, failed jobs, "
            "degraded services, abnormal status transitions, or repeated warning "
            "signals. Only report an incident when the evidence is concrete enough "
            "to justify filing one."
        ),
        severity_default="medium",
        interval_seconds=600,
    ),
)


def list_detector_templates() -> list[DetectorTemplate]:
    return list(_TEMPLATES)
