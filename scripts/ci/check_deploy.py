"""Check the Flux manifests in deploy/ the way Flux would apply them

Uses deploy/clusters/example as the cluster: checks it points at
deploy/flux the way a cluster's own repository would, then builds each
layer with kustomize, substitutes ${VARS} from cluster-settings.yaml
as Flux's postBuild does, checks the result against the Kubernetes and CRD
schemas with kubeconform, and renders every HelmRelease's chart with its
values so a bad value fails here rather than in the cluster. Alloy's
config is checked with alloy itself, and the alert rules with promtool,
including their unit tests in deploy/monitoring/alerts/tests.

Needs kustomize, kubeconform, helm, promtool and alloy on the PATH, see
install-tools.sh"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
FLUX = DEPLOY / "flux"
OPERATORS = DEPLOY / "operators"
CLUSTER = DEPLOY / "clusters" / "example"
RULE_TESTS = DEPLOY / "monitoring" / "alerts" / "tests"
KUBERNETES_VERSION = "1.33.0"
CRD_SCHEMAS = (
    "https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/"
    "{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json"
)
VARIABLE = re.compile(r"(\$)?\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def substitute(text: str, settings: dict) -> str:
    missing = set()

    def replace(match):
        if match.group(1):
            # $${...} is how Flux is told to leave it alone
            return "${" + match.group(2) + "}"
        if match.group(2) not in settings:
            missing.add(match.group(2))
            return match.group(0)
        return str(settings[match.group(2)])

    result = VARIABLE.sub(replace, text)
    if missing:
        names = ", ".join(sorted(missing))
        raise SystemExit(f"Not in cluster-settings.yaml: {names}")
    return result


def layer_paths(settings: dict) -> list[Path]:
    """Each layer's directory, with the settings filled in, as they pick
    each operator's install or existing directory"""
    text = substitute((FLUX / "layers.yaml").read_text(), settings)
    layers = yaml.safe_load_all(text)
    return [ROOT / layer["spec"]["path"] for layer in layers if layer]


def check_example() -> None:
    """The example cluster applies deploy/flux from the qsology
    GitRepository, and every layer comes from that same source"""
    docs = [
        doc
        for doc in yaml.safe_load_all((CLUSTER / "qsology.yaml").read_text())
        if doc
    ]
    source = next(doc for doc in docs if doc["kind"] == "GitRepository")
    entry = next(doc for doc in docs if doc["kind"] == "Kustomization")
    ref = {"kind": "GitRepository", "name": source["metadata"]["name"]}
    name = entry["metadata"]["name"]
    problems = []
    if entry["spec"]["sourceRef"] != ref:
        problems.append(f"{name} isn't from {ref}")
    if ROOT / entry["spec"]["path"] != FLUX:
        problems.append(f"{name} doesn't apply deploy/flux")
    for layer in yaml.safe_load_all((FLUX / "layers.yaml").read_text()):
        if layer and layer["spec"]["sourceRef"] != ref:
            layer_name = layer["metadata"]["name"]
            problems.append(f"layer {layer_name} isn't from {ref}")
    if problems:
        raise SystemExit("\n".join(problems))
    print("  example cluster: applies deploy/flux")


def run(*command: str) -> str:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        output = (result.stdout + result.stderr).strip()
        raise SystemExit(f"{command[0]} failed:\n{output}")
    return result.stdout


def clone(source: dict, work: Path) -> Path:
    """The GitRepository's pinned commit, checked out under work"""
    checkout = work / "git" / source["metadata"]["name"]
    if not checkout.exists():
        commit = source["spec"]["ref"]["commit"]
        run("git", "init", "--quiet", str(checkout))
        git = ("git", "-C", str(checkout))
        url = source["spec"]["url"]
        run(*git, "fetch", "--quiet", "--depth=1", url, commit)
        run(*git, "checkout", "--quiet", "FETCH_HEAD")
    return checkout


def render_chart(release: dict, sources: dict, work: Path) -> None:
    spec = release["spec"]
    name = release["metadata"]["name"]
    namespace = release["metadata"]["namespace"]
    values = work / f"{name}.values.yaml"
    values.write_text(yaml.safe_dump(spec.get("values", {})))
    if "chartRef" in spec:
        source = sources[("OCIRepository", spec["chartRef"]["name"])]
        ref = source["spec"]["ref"]["tag"]
        chart = [source["spec"]["url"], "--version", ref]
    elif spec["chart"]["spec"]["sourceRef"]["kind"] == "GitRepository":
        chart_spec = spec["chart"]["spec"]
        source = sources[("GitRepository", chart_spec["sourceRef"]["name"])]
        checkout = clone(source, work)
        chart = [str(checkout / chart_spec["chart"])]
    else:
        chart_spec = spec["chart"]["spec"]
        source = sources[("HelmRepository", chart_spec["sourceRef"]["name"])]
        chart = [
            chart_spec["chart"],
            "--repo",
            source["spec"]["url"],
            "--version",
            chart_spec["version"],
        ]
    run("helm", "template", name, *chart, "-n", namespace, "-f", str(values))
    print(f"  chart {name}: renders")


def check_alloy(release: dict, work: Path) -> None:
    values = release["spec"].get("values", {})
    content = values.get("alloy", {}).get("configMap", {}).get("content")
    if not content:
        return
    name = release["metadata"]["name"]
    config = work / f"{name}.alloy"
    config.write_text(content)
    run("alloy", "validate", str(config))
    print(f"  alloy config {name}: valid")


def check_rules(rules: list[dict], work: Path) -> None:
    """promtool reads plain rule files, so each PrometheusRule's spec is
    written out as one, named after it for the unit tests to load"""
    directory = work / "rules"
    directory.mkdir()
    paths = []
    for rule in rules:
        path = directory / f"{rule['metadata']['name']}.yaml"
        path.write_text(yaml.safe_dump(rule["spec"]))
        paths.append(str(path))
    if not paths:
        return
    run("promtool", "check", "rules", "--lint=all", *paths)
    print(f"  alert rules: {len(paths)} files valid")
    tests = sorted(RULE_TESTS.glob("*.yaml"))
    for test in tests:
        (directory / test.name).write_text(test.read_text())
    if tests:
        names = [str(directory / test.name) for test in tests]
        run("promtool", "test", "rules", *names)
        print(f"  alert rule tests: {len(tests)} files pass")


def main() -> int:
    settings = yaml.safe_load((CLUSTER / "cluster-settings.yaml").read_text())
    settings = settings["data"]
    sources = {
        (doc["kind"], doc["metadata"]["name"]): doc
        for doc in yaml.safe_load_all((FLUX / "sources.yaml").read_text())
        if doc
    }
    check_example()
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        files = [
            CLUSTER / "cluster-settings.yaml",
            CLUSTER / "qsology.yaml",
            CLUSTER / "image-automation.yaml",
            FLUX / "sources.yaml",
            FLUX / "layers.yaml",
        ]
        releases = []
        rules = []
        paths = layer_paths(settings)
        for path in paths:
            built = substitute(run("kustomize", "build", str(path)), settings)
            name = path.relative_to(DEPLOY).as_posix().replace("/", "-")
            out = work / f"{name}.yaml"
            out.write_text(built)
            files.append(out)
            docs = [doc for doc in yaml.safe_load_all(built) if doc]
            releases += [doc for doc in docs if doc["kind"] == "HelmRelease"]
            rules += [doc for doc in docs if doc["kind"] == "PrometheusRule"]
            print(f"  layer {path.relative_to(ROOT)}: builds")
        # The operator directories the example cluster doesn't use still
        # have to build, for clusters that do
        for path in sorted(OPERATORS.glob("*/*/")):
            if path not in paths:
                substitute(run("kustomize", "build", str(path)), settings)
                print(f"  unused {path.relative_to(ROOT)}: builds")
        print(
            run(
                "kubeconform",
                "-strict",
                "-summary",
                "-kubernetes-version",
                KUBERNETES_VERSION,
                "-schema-location",
                "default",
                "-schema-location",
                CRD_SCHEMAS,
                *map(str, files),
            ).strip()
        )
        for release in releases:
            render_chart(release, sources, work)
            check_alloy(release, work)
        check_rules(rules, work)
    return 0


if __name__ == "__main__":
    sys.exit(main())
