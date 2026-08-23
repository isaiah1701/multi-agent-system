"""Small, maintainable domain signals for local platform-question classification."""

from __future__ import annotations


# These are common operator shorthands, not a reproduction of Kubernetes vocabulary.
ALIASES: dict[str, frozenset[str]] = {
    "kubernetes": frozenset({"k8s", "kube", "kubernetes"}),
    "pod_disruption_budget": frozenset({"pdb", "pdbs", "poddisruptionbudget", "pod disruption budget", "pod disruption budgets"}),
    "horizontal_pod_autoscaler": frozenset({"hpa", "hpas", "horizontalpodautoscaler", "horizontal pod autoscaler"}),
    "vertical_pod_autoscaler": frozenset({"vpa", "vpas", "verticalpodautoscaler", "vertical pod autoscaler"}),
    "persistent_volume_claim": frozenset({"pvc", "pvcs", "persistentvolumeclaim", "persistent volume claim", "persistent volume claims"}),
    "persistent_volume": frozenset({"pv", "pvs", "persistentvolume", "persistent volume"}),
    "resource_management": frozenset({"resource request", "resource requests", "resource limit", "resource limits"}),
    "service_account": frozenset({"sa", "serviceaccount", "service account"}),
    "config_map": frozenset({"cm", "configmap", "config map"}),
    "namespace": frozenset({"ns", "namespace", "namespaces"}),
    "service": frozenset({"svc", "svcs"}),
    "ingress": frozenset({"ing", "ingress"}),
    "container_networking": frozenset({"cni", "container network interface"}),
    "container_storage": frozenset({"csi", "container storage interface"}),
    "out_of_memory": frozenset({"oom", "ooming", "oom ing", "oomkill", "oomkilled", "oom killed"}),
    "crash_loop_backoff": frozenset({"crashloop", "crashloops", "crashlooping", "crash loop", "crash loop back off", "crashloopbackoff"}),
    "managed_kubernetes": frozenset({"eks", "aks", "gke"}),
}

# Unambiguous Kubernetes API and operational concepts. Plurals are handled by
# normalisation, and the corpus adds vocabulary beyond these stable anchors.
KUBERNETES_TERMS = frozenset(
    {
        "apiserver", "cluster", "container", "containers", "cronjob", "daemonset", "deployment", "egress",
        "ingress", "kubeadm", "kubectl", "kubelet", "namespace", "networkpolicy", "node", "nodes", "pod",
        "pods", "replicaset", "resourcequota", "scheduler", "secret", "sidecar", "statefulset", "storageclass",
        "taint", "toleration", "workload", "workloads",
    }
)

ECOSYSTEM_TERMS = frozenset(
    {
        "argocd", "argo cd", "backstage", "cert manager", "externaldns", "flux", "helm", "istio", "karpenter",
        "linkerd", "cluster autoscaler", "service mesh", "prometheus", "grafana", "opentelemetry", "loki",
    }
)

MANAGED_KUBERNETES_TERMS = frozenset({"eks", "aks", "gke", "managed kubernetes"})

# These need contextual support because they also occur outside platform engineering.
ADJACENT_TERMS = frozenset(
    {
        "alb", "aws", "azure", "gcp", "iam", "instance type", "load balancer", "node group", "node groups",
        "security group", "security groups", "subnet", "subnets", "vpc", "cloud storage", "terraform", "opentofu",
        "gitops", "ci cd", "container registry", "containers", "docker", "dns", "linux", "networking", "storage",
        "tls", "metrics", "logs", "traces", "slo", "sli", "postgres", "postgresql",
    }
)

AI_PLATFORM_TERMS = frozenset(
    {
        "gpu", "gpu operator", "nvidia", "nvidia gpu operator", "vllm", "triton", "model serving", "inference",
        "embedding service", "embedding services", "vector database", "vector databases", "langfuse", "mlflow", "ray", "kubeflow",
    }
)

PLATFORM_CONTEXT_TERMS = frozenset(
    {
        "app", "application", "architecture", "cluster", "configure", "containerise", "containerize", "deploy", "deployment",
        "drain", "expose", "infrastructure", "manifest", "node", "operate", "platform", "provision", "run", "scale",
        "schedule", "service", "troubleshoot", "worker", "workers", "workload",
    }
)

# Strong non-platform subjects keep a permissive guardrail from becoming an
# unrestricted general-purpose assistant.
CLEARLY_IRRELEVANT_TERMS = frozenset(
    {
        "biceps", "celebrity", "cook", "cooking", "exercise", "exercises", "football", "gym", "lasagna", "poem",
        "poetry", "recipe", "recipes", "restaurant", "soccer", "workout", "workouts", "world cup",
    }
)

CLEARLY_IRRELEVANT_PHRASES = frozenset(
    {"capital of", "docker desktop", "french revolution", "history of rome", "love poem", "write a poem"}
)
