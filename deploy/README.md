# Deploying qsology on Kubernetes

Everything here is applied by [Flux](https://fluxcd.io). Change the cluster by
changing git; nothing should be `kubectl apply`'d by hand. The one exception
is setting up OpenBao, which holds the secrets and so can't come from git (see
[First deployment](#first-deployment)).

The manifests in this directory are the same for every cluster. What's
particular to one cluster (its domains, storage classes, where telemetry
goes, which image it runs) lives in that cluster's own repository, usually
private, which Flux is bootstrapped from and which pulls this one in. See
[A cluster's own repository](#a-clusters-own-repository); an example to
copy is in [`clusters/example`](clusters/example).

## What runs where

```
                    ┌────────────── gateway (Envoy Gateway) ──────────────┐
  users ──HTTPS──▶  │  ${QSOLOGY_DOMAIN}       →  qsology-web  (Django)   │
                    │  ${QSOLOGY_S3_DOMAIN}    →  rustfs       (S3 API)   │
                    └─────────────────────────────────────────────────────┘
  namespace qsology
    qsology-web      Django under gunicorn, 3–10 pods (HPA), PDB min 2
    qsology-worker   celery workers, 2–20 pods (HPA)
    qsology-beat     celery beat, exactly 1 pod, Recreate
    celery-exporter  celery's metrics, from task events and queue lengths
    qsology-db       PostgreSQL 18 (CloudNativePG), primary + sync replica
    valkey-{0,1,2}   Valkey, primary + 2 replicas: celery broker (db 0), Django cache (db 1)
    valkey-sentinel  3 Sentinels, promote a replica if the primary goes
    valkey-haproxy   2 HAProxy pods, always pointing at the current primary
    rustfs-{0,1,2}   RustFS, 3 drives erasure coded 2+1
  namespace qsology-openbao        OpenBao, 3 nodes, Raft, TLS
  namespace qsology-observability  Grafana Alloy, 3 pods: metrics → Mimir, traces → Tempo,
                                   logs → Loki, alert rules → Mimir's ruler; kube-state-metrics
```

| Component | How | Version |
|---|---|---|
| Gateway | Envoy Gateway (Gateway API) | 1.9.2 |
| Certificates | cert-manager: Let's Encrypt outside, a private CA inside | 1.21.2 |
| PostgreSQL | CloudNativePG + Barman Cloud plugin | 1.30.1 / 0.15.1 |
| Object storage | RustFS (own StatefulSet) | 1.0.1 |
| Cache and broker | Valkey + Sentinel + HAProxy (valkey-helm, unreleased 1.0 from git) | 9.1.2 |
| Secrets | OpenBao + External Secrets Operator | 2.7.1 / 2.12.0 |
| Telemetry | Grafana Alloy (chart 1.13.1) | 1.20.1 |
| Metrics | prometheus_client (app), celery-exporter, kube-state-metrics (chart 8.6.0) | 0.26.0 / 0.12.2 / 2.20.0 |
| Network policies | Cilium (the cluster's CNI, not installed from here) | – |

Every image and chart is pinned. Images we build ourselves are pinned by
Flux's image automation (below); the others are pinned by tag or digest.

### Layers

Flux applies [`flux/layers.yaml`](flux/layers.yaml), each layer waiting for
the ones it depends on to be healthy:

1. `qsology-namespaces`: qsology's own namespaces ([`infrastructure/namespaces`](infrastructure/namespaces))
2. The operators, one layer each, installed by qsology or already in the cluster (see [Operators](#operators)): `qsology-envoy-gateway`, `qsology-cert-manager`, `qsology-cloudnative-pg`, `qsology-barman-cloud`, `qsology-external-secrets`, `qsology-prometheus-operator-crds`, `qsology-kube-state-metrics` ([`operators`](operators))
3. `qsology-alloy`: Grafana Alloy ([`infrastructure/alloy`](infrastructure/alloy))
4. `qsology-infra-configs`: issuers, the Gateway ([`infrastructure/configs`](infrastructure/configs))
5. `qsology-infra-openbao`: OpenBao and the `ClusterSecretStore` ESO reads it through ([`infrastructure/openbao`](infrastructure/openbao))
6. `qsology-data`: the namespace's network policies, Valkey, PostgreSQL, RustFS and its buckets ([`apps/data`](apps/data)). Never pruned, so removing a file can't delete a database.
7. `qsology-migrate`: the app's settings and secrets, then `manage.py migrate` as a Job ([`apps/migrate`](apps/migrate))
8. `qsology-app`: web, workers, beat, route ([`apps/qsology`](apps/qsology))
9. `qsology-monitoring`: the ServiceMonitors and PodMonitors Alloy scrapes, and the alert rules ([`monitoring`](monitoring))

### Operators

qsology needs seven things a cluster often has already. For each, a setting
in `cluster-settings.yaml` says whether qsology installs its own copy
(`install`) or uses the cluster's (`existing`):

| Setting | What | With `existing`, also set |
|---|---|---|
| `ENVOY_GATEWAY` | Envoy Gateway and the Gateway API CRDs | `ENVOY_GATEWAY_NAMESPACE` (where its Envoy pods run), `GATEWAY_CLASS` |
| `CERT_MANAGER` | cert-manager, with Gateway API support on | `CERT_MANAGER_NAMESPACE` (its cluster resource namespace) |
| `CLOUDNATIVE_PG` | the CloudNativePG operator | `CLOUDNATIVE_PG_NAMESPACE` |
| `BARMAN_CLOUD` | CloudNativePG's Barman Cloud plugin (WAL archive, backups) | – (it runs beside the operator) |
| `EXTERNAL_SECRETS` | the External Secrets Operator | `EXTERNAL_SECRETS_NAMESPACE`, `EXTERNAL_SECRETS_SERVICE_ACCOUNT` |
| `PROMETHEUS_OPERATOR_CRDS` | ServiceMonitor, PodMonitor, PrometheusRule CRDs | – |
| `KUBE_STATE_METRICS` | kube-state-metrics, for the Kubernetes alerts | – (its metrics must reach the same Mimir tenant) |

Each operator's layer applies `operators/<name>/install` or
`operators/<name>/existing`:

- **`install`** is a standalone install of that operator alone (namespace,
  HelmRelease, and for Envoy Gateway the GatewayClass), in a `qsology-`
  namespace unless you change it.
- **`existing`** is empty. Its `kustomization.yaml` lists what the cluster's
  copy has to provide (versions, flags, where it runs). Read it before
  switching.

Either way the layer becomes ready, so nothing after it changes. Two copies
of one operator in a cluster fight over its CRDs and webhooks, so if the
cluster already runs one, use it.

Operator layers are never pruned. Uninstalling an operator can delete its
CRDs, and with them every certificate, secret or database made from them, so
moving a running cluster from `install` to `existing` is a migration, not a
setting change: setting `existing` leaves qsology's copy running (Flux stops
managing it), and removing it is up to you, once you're sure the other copy
owns the CRDs.

Cluster-specific values (domains, storage classes, where telemetry goes, the
image) come from the cluster's `qsology-cluster-settings` ConfigMap. Flux substitutes
them into the manifests as `${NAME}`.

### Releasing

1. A merge to `main` builds `ghcr.io/qsology/qsology:main-<UTC timestamp>-<sha>` ([`.github/workflows/image.yml`](../.github/workflows/image.yml)).
2. Each cluster's image automation ([`image-automation.yaml`](clusters/example/image-automation.yaml)) sees it and commits the new tag to `QSOLOGY_IMAGE` in its `cluster-settings.yaml`, in the cluster's own repository.
3. Flux applies that commit: the migration Job runs first, then the app rolls out.

Because the new tag is committed, the cluster's repository always says what's
running. Rolling back means reverting that commit. Flux never writes to this
repository.

## A cluster's own repository

This repository is open source, so it holds nothing about any one cluster.
Each cluster gets a repository of its own, which Flux is bootstrapped from.
It holds that cluster's settings, its image automation, and a `GitRepository`
pointing back here:

```
your-fleet-repo/                    (private)
└── clusters/
    └── production/                 ← copied from deploy/clusters/example
        ├── kustomization.yaml      what Flux applies, written by you
        ├── flux-system/            written by flux bootstrap
        ├── cluster-settings.yaml   domains, storage, telemetry, the image
        ├── qsology.yaml            this repo as a source, and what to apply from it
        └── image-automation.yaml   follows new images, commits their tags here
```

```
 your-fleet-repo ──bootstrap──▶ flux-system
   cluster-settings.yaml ──────────────────────────┐ ${...}
   qsology.yaml                                    │
     GitRepository qsology ──▶ github.com/qsology/qsology, deploy/ only
     Kustomization qsology-flux ──▶ deploy/flux
                                     sources.yaml  chart repositories
                                     layers.yaml   qsology-namespaces … qsology-monitoring
                                                     └─▶ deploy/operators, infrastructure,
                                                         apps, monitoring
```

Only [`qsology.yaml`](clusters/example/qsology.yaml) refers to this
repository; the layers come from it, so a change merged here reaches every
cluster that follows it.

### Setting one up

1. Make a private repository, and copy [`clusters/example`](clusters/example)
   into it as `clusters/production` (or any name; then change `update.path`
   in `image-automation.yaml` to match).
2. Fill in `cluster-settings.yaml`. Every value is a placeholder.
3. Decide how closely to follow this repository (`ref` in `qsology.yaml`):
   - `branch: main`: everything merged to `deploy/` goes out within 10
     minutes. Fine when you run qsology yourself.
   - `commit: <sha>`: nothing changes until you move it. Better for anyone
     else's cluster, and for reviewing what's about to change
     (`git diff <old>..<new> -- deploy/`).
4. If you run your own build of the image, change it in
   `cluster-settings.yaml` and `image-automation.yaml`.
5. Commit, and bootstrap Flux from that repository (see
   [First deployment](#first-deployment)).

### Changing things for one cluster

- **Values** that are already settings, including which operators to
  install: change `cluster-settings.yaml`.
- **Anything else**: add `patches` to the `qsology-flux` Kustomization in
  `qsology.yaml`. A patch there changes the layers themselves (drop one,
  change its interval); a patch that adds to a layer's own `spec.patches`
  changes what that layer deploys. `qsology.yaml` has an example of each:
  removing the `qsology-monitoring` layer, and raising the web app's HPA maximum.
- **More than a patch**: add your own Kustomizations to the cluster's
  repository, beside `qsology.yaml`, with `dependsOn` on a layer if they
  need it. Changes others could use belong here, as a pull request.

### Sharing a cluster

Every name qsology creates outside its own resources starts with `qsology`,
so it can sit beside other things in the same cluster and the same
`flux-system`:

| What | Names |
|---|---|
| Namespaces | `qsology` (the app), `qsology-openbao`, `qsology-observability`, `qsology-gateway`; for operators qsology installs, `qsology-cert-manager`, `qsology-external-secrets`, `qsology-cnpg-system`, `qsology-envoy-gateway-system` (settings) |
| Flux, in `flux-system` | `qsology` (GitRepository), `qsology-flux`, the layers `qsology-namespaces` … `qsology-monitoring`, the chart sources `qsology-jetstack` … `qsology-valkey-helm`, the ConfigMap `qsology-cluster-settings`, the image automation `qsology`, the `prometheus-operator-crds` release |
| Cluster-wide | ClusterIssuers `qsology-letsencrypt`, `qsology-internal`, `qsology-selfsigned`; ClusterSecretStore `qsology-openbao`; GatewayClass `qsology-envoy` (`GATEWAY_CLASS`) |

Prefixed names don't make two copies of an operator safe; use the cluster's
own instead (see [Operators](#operators)).

### More than one cluster

Give each its own directory under `clusters/`, each with its own settings,
image automation (`update.path` set to its directory) and `flux bootstrap
--path`. A staging cluster can follow `branch: main` while production pins a
`commit`, moved on once staging looks right.

### Trying a change before it's merged

Point a cluster's `GitRepository` at your branch (`ref: branch: my-change`,
or your fork's `url`), commit that in the cluster's repository, and put it
back afterwards.

To try changes that aren't committed anywhere, push your working copy of
`deploy/` as an OCI artifact. Copy it into an empty directory first, so the
paths inside stay `./deploy/...`:

```sh
stage=$(mktemp -d) && cp -r deploy "$stage/"
flux push artifact oci://ghcr.io/<you>/qsology-deploy:test --path="$stage" \
  --source="$(git config --get remote.origin.url)" \
  --revision="$(git branch --show-current)@sha1:$(git rev-parse HEAD)"
```

Then, in the cluster's `qsology.yaml`, replace the `GitRepository` with an
`OCIRepository` of the same name (`url: oci://ghcr.io/<you>/qsology-deploy`,
`ref: {tag: test}`), set `qsology-flux`'s `sourceRef.kind` to
`OCIRepository`, and patch the layers to match:

```yaml
  patches:
    - target:
        kind: Kustomization
      patch: |
        - op: replace
          path: /spec/sourceRef/kind
          value: OCIRepository
```

Push again after each edit and `flux reconcile source oci qsology`.

To try code changes, build and push an image yourself:

```sh
task image:login   # once; the gh token needs write:packages
task image:push    # IMAGE=ghcr.io/<you>/qsology to push somewhere else
```

It's tagged `dev-<UTC timestamp>-<sha>` (with `-dirty` if there were
uncommitted changes), which the image automation ignores. Set
`QSOLOGY_IMAGE` to it in the cluster's `cluster-settings.yaml`, without the
`$imagepolicy` marker, or Flux puts the newest `main-` image back. Restore
the marker to go back to following `main`.

### Flux pushing to `main`

The image automation commits straight to `main` in the cluster's repository.
If that branch is protected, either let Flux's deploy key bypass the rule, or
set `push.branch` to e.g. `flux/image-updates` and merge the pull requests
it opens (by hand, or with auto-merge).

### Metrics, traces and logs

Everything leaves the cluster through Alloy
([`infrastructure/alloy/alloy.yaml`](infrastructure/alloy/alloy.yaml)),
to the Mimir, Tempo and Loki named in `cluster-settings.yaml`:

- **Traces.** Envoy starts the trace and sends `traceparent` on to Django
  (`TRACE_SAMPLING_PERCENT` sets how many requests). Django carries it into
  the celery tasks it queues, and into PostgreSQL, Valkey, S3 and HTTP calls
  ([`config/telemetry.py`](../config/telemetry.py)). All of it goes over OTLP
  to Alloy, then Tempo.
- **Metrics.** Alloy scrapes everything through ServiceMonitors and
  PodMonitors ([`monitoring/monitors.yaml`](monitoring/monitors.yaml)), and
  sends it to Mimir with a `cluster` label:
  - **The web app**: requests by URL pattern and status, latency, requests
    in progress ([`config/metrics.py`](../config/metrics.py)). Each gunicorn
    worker counts on its own; with `PROMETHEUS_MULTIPROC_DIR` set they write
    to files, and the gunicorn master adds them up and serves them on
    `:9100`, which the gateway doesn't route to
    ([`config/prometheus.py`](../config/prometheus.py)). The middleware
    works under ASGI too, and with gunicorn's uvicorn workers the hooks are
    the same; without gunicorn, run `python -m config.prometheus 9100`
    beside the server.
  - **Celery**, through celery-exporter: tasks sent, started, succeeded,
    failed and retried by name, runtimes, workers up, queue lengths. The
    workers send task events for it (`CELERY_WORKER_SEND_TASK_EVENTS`).
  - **Kubernetes**: kube-state-metrics (restarts, OOM kills, replicas, jobs,
    HPAs) and every kubelet (volume usage, container CPU and memory).
  - **The rest**: Envoy, PostgreSQL, Valkey, OpenBao, cert-manager and Alloy
    itself.
  - **RustFS** has nothing to scrape; it pushes its metrics over OTLP.
- **Logs.** Alloy reads every pod's logs through the Kubernetes API and sends
  them to Loki. The trace id in the app's `trace=` and Envoy's `traceparent`
  is kept as structured metadata, so a log line links to its trace.

### Alerts

The rules are PrometheusRules in [`monitoring/alerts`](monitoring/alerts).
Alloy loads them into Mimir's ruler, limits their queries to this cluster's
series and adds a `cluster` label to what fires. Mimir's Alertmanager then
routes the alerts; its receivers aren't configured from here.

| File | Covers |
|---|---|
| [`qsology.yaml`](monitoring/alerts/qsology.yaml) | web up, error rate, slow routes, the gateway's backends; celery workers, queue backlog, failing tasks, beat; failed migrations |
| [`data.yaml`](monitoring/alerts/data.yaml) | PostgreSQL up, HA, replication, WAL archiving, backups, connections, wraparound; Valkey primary, replicas, memory, AOF; RustFS quorum |
| [`platform.yaml`](monitoring/alerts/platform.yaml) | OpenBao sealed, Raft, audit; gateway up and errors; certificates; crash loops, OOM kills, replicas, volumes filling; the monitoring itself |

- `severity: critical` is for now, users are or soon will be affected;
  `warning` can wait for working hours.
- `Watchdog` always fires. Route it to a dead man's switch, which alerts when
  it stops arriving, as that means alerting itself is broken.
  `ClusterMetricsMissing` fires when nothing at all arrives from the cluster.
- Every rule is checked by `promtool` in `task deploy:check`, and the most
  important have unit tests in
  [`monitoring/alerts/tests`](monitoring/alerts/tests/alerts.yaml). Add a
  test with any new rule.

## Encryption

| What | In transit | At rest |
|---|---|---|
| Users → gateway | TLS (Let's Encrypt), HTTP redirected | – |
| App → PostgreSQL | TLS 1.3, `verify-full` against the cluster's CA; plain connections refused | `ENCRYPTED_STORAGE_CLASS` (PostgreSQL has no encryption of its own) |
| PostgreSQL replication | TLS with client certificates | as above |
| WAL archive and backups | plain HTTP inside the namespace | RustFS server-side encryption, keys in OpenBao transit |
| Exports | through the gateway, TLS | RustFS server-side encryption |
| App → Valkey, replication | plain, inside the namespace; password per user, network policies | `STORAGE_CLASS` (queues and cache only) |
| OpenBao | TLS 1.3, private CA | sealed storage, on `ENCRYPTED_STORAGE_CLASS` |

## First deployment

You need:
- Kubernetes 1.30 or newer, with Cilium as the CNI (with Hubble, to see what
  the network policies drop) and a LoadBalancer (for the gateway).
- The storage classes named in `cluster-settings.yaml`.
- DNS for both domains pointing at the gateway's load balancer.
- Mimir, Loki and Tempo for Alloy to send to.

### 1. Settings and Flux

Set up the cluster's own repository
([Setting one up](#setting-one-up)), then bootstrap Flux from it. The image
automation controllers are extra components, and they need a deploy key that
can push to that repository:

```sh
flux bootstrap github \
  --owner=<you> --repository=<your-fleet-repo> --private --branch=main \
  --path=clusters/production \
  --components-extra=image-reflector-controller,image-automation-controller \
  --read-write-key
```

This repository is public, so Flux reads it without a key. For a fork that
isn't, give the `qsology` GitRepository a `secretRef`
(`flux create secret git`).

The infrastructure layers come up on their own. `qsology-infra-openbao` is ready once
the release is installed. Everything after it waits on secrets, which come
next.

### 2. Initialise and unseal OpenBao

The OpenBao nodes find each other through `retry_join`.

```sh
kubectl -n qsology-openbao exec -ti openbao-0 -- bao operator init
```

This prints 5 unseal keys and a root token. Keep the keys apart, with
different people or in different places; any 3 of them unseal OpenBao. Then
unseal each node with 3 of the keys:

```sh
for pod in openbao-0 openbao-1 openbao-2; do
  for i in 1 2 3; do kubectl -n qsology-openbao exec -ti $pod -- bao operator unseal; done
done
kubectl -n qsology-openbao exec -ti openbao-0 -- sh -c 'BAO_TOKEN=<root token> bao operator raft list-peers'
```

> **OpenBao has to be unsealed again whenever a pod restarts.** That is the
> price of not trusting anything else with the key. The plan is a `transit`
> seal against a second OpenBao cluster, which will unseal this one. OpenBao
> also has a `static` seal, but it keeps the key in a Kubernetes Secret, which
> defeats the point.

### 3. Configure OpenBao

Run these with the root token, from a shell on `openbao-0`:

```sh
kubectl -n qsology-openbao exec -ti openbao-0 -- sh
export BAO_TOKEN=<root token>

bao audit enable file file_path=/openbao/audit/audit.log

# Secrets for the apps, and encryption keys for RustFS
bao secrets enable -path=secret kv-v2
bao secrets enable -path=transit transit

bao auth enable kubernetes
bao write auth/kubernetes/config \
  kubernetes_host="https://$KUBERNETES_SERVICE_HOST:$KUBERNETES_SERVICE_PORT"

# The External Secrets Operator reads qsology's secrets
bao policy write external-secrets - <<'EOF'
path "secret/data/qsology/*"     { capabilities = ["read"] }
path "secret/metadata/qsology/*" { capabilities = ["read", "list"] }
EOF
# EXTERNAL_SECRETS_SERVICE_ACCOUNT and EXTERNAL_SECRETS_NAMESPACE
bao write auth/kubernetes/role/external-secrets \
  bound_service_account_names=external-secrets \
  bound_service_account_namespaces=qsology-external-secrets \
  policies=external-secrets ttl=1h

# RustFS creates and uses its own keys in transit (but can't delete them)
# and keeps its notes about them under secret/rustfs/kms
bao policy write rustfs - <<'EOF'
path "transit/keys"           { capabilities = ["list"] }
path "transit/keys/*"         { capabilities = ["create", "read", "update"] }
path "transit/encrypt/*"      { capabilities = ["update"] }
path "transit/decrypt/*"      { capabilities = ["update"] }
path "transit/rewrap/*"       { capabilities = ["update"] }
path "transit/datakey/*"      { capabilities = ["update"] }
path "secret/data/rustfs/kms/*"     { capabilities = ["create", "read", "update"] }
path "secret/metadata/rustfs/kms/*" { capabilities = ["read", "list"] }
EOF
bao write auth/kubernetes/role/rustfs \
  bound_service_account_names=rustfs \
  bound_service_account_namespaces=qsology \
  policies=rustfs ttl=1h
```

### 4. Write the secrets

Generate each value; don't reuse them.

```sh
pw() { head -c 32 /dev/urandom | base64 | tr -d '/+=' ; }
bao kv put secret/qsology/django      secret_key="$(head -c 64 /dev/urandom | base64 | tr -d '\n')"
bao kv put secret/qsology/valkey      default="$(pw)" replicator="$(pw)" qsology="$(pw)" sentinel="$(pw)"
bao kv put secret/qsology/rustfs-root access_key="admin-$(pw | head -c 8)" secret_key="$(pw)"
```

RustFS starts once `rustfs-root` exists. Then make one user for the app and
one for PostgreSQL's backups in the RustFS console:

```sh
kubectl -n qsology port-forward svc/rustfs 9001
```

Open http://localhost:9001 and log in with the root keys. Create these users,
and give each a policy that only allows `s3:*` on its own bucket:

| User | Bucket |
|---|---|
| `qsology-exports` | `qsology-exports` |
| `postgres-backups` | `postgres-backups` |

Then store their keys:

```sh
bao kv put secret/qsology/rustfs-exports access_key=... secret_key=...
bao kv put secret/qsology/rustfs-backups access_key=... secret_key=...
```

The External Secrets Operator picks secrets up within an hour. To make it
check now:

```sh
kubectl -n qsology annotate externalsecret --all force-sync=$(date +%s) --overwrite
```

The Valkey users are `default` (admin, also used by the exporter and
HAProxy's health check), `replicator` (replication and the Sentinels) and
`qsology` (the app); `sentinel` is the Sentinels' own password.

After that the rest of the layers come up: PostgreSQL, then the migrations,
then the app. Watch them with `flux get kustomizations --watch`.

### 5. Check it

```sh
kubectl -n qsology get pods
kubectl -n qsology get cluster qsology-db   # "Cluster in healthy state", 2 ready
kubectl cnpg -n qsology status qsology-db   # WAL archiving working (kubectl cnpg plugin)
curl -I https://$QSOLOGY_DOMAIN/healthz
```

## Day to day

- **Scaling.**
  - Web and workers scale themselves on CPU; change the bounds in their HPAs.
  - Workers can also be scaled on queue length with KEDA's redis scaler pointed at `valkey-haproxy`.
- **OpenBao restarts.** Unseal the restarted pod (step 2) until auto-unseal is set up.
  - Until then, RustFS can't reach its encryption keys while OpenBao is sealed.
- **Restoring PostgreSQL.**
  - Make a new `Cluster` with `bootstrap.recovery` from the `qsology-backups` ObjectStore, optionally to a point in time.
  - Give it `plugins` with `isWALArchiver: true` again, since recovery doesn't turn archiving back on.
  - See the CloudNativePG Barman Cloud plugin docs.
- **Rotating a Valkey password.** Change it in OpenBao and force-sync the
  ExternalSecrets. Valkey reads its users at start, so restart it a pod at a
  time (Sentinel fails over as the primary goes), then the app:

  ```sh
  kubectl -n qsology rollout restart statefulset/valkey statefulset/valkey-sentinel deploy/valkey-haproxy
  kubectl -n qsology rollout restart deploy/qsology-web deploy/qsology-worker deploy/qsology-beat
  ```

### Network policies and Hubble

Cilium enforces the policies in
[`apps/data/network-policies.yaml`](apps/data/network-policies.yaml) and
[`infrastructure/openbao/network-policies.yaml`](infrastructure/openbao/network-policies.yaml).
Every pod in `qsology` and `qsology-openbao` is denied anything they don't allow,
both ways. When something can't connect, ask Hubble what it dropped. The
`hubble` CLI talks to Hubble Relay, so port-forward to it first:

```sh
cilium hubble port-forward &

# Everything dropped, as it happens
hubble observe -n qsology --verdict DROPPED --follow
hubble observe -n qsology-openbao --verdict DROPPED --follow

# The last drops for one pod, with the policy verdict for each
hubble observe -n qsology --pod qsology-worker --verdict DROPPED --last 50
hubble observe -n qsology --type policy-verdict --last 50

# Outside sites a pod tried to reach (hostname rules)
hubble observe -n qsology --to-fqdn "*" --verdict DROPPED --last 50
```

A drop names the source and destination pods (or `world` for outside) and
the port. Allow it by adding a rule to the policy file and committing it;
Flux applies it. Check Cilium accepted the policies with
`kubectl -n qsology get ciliumnetworkpolicies`, where `VALID` should be
`True`.

kubectl port-forward isn't affected by the policies, so the RustFS console
and OpenBao are always reachable that way.

## Known limits

- **Valkey's chart is unreleased.** Sentinel and HAProxy are only in valkey-helm's coming 1.0, so the chart comes from git at a pinned commit. Move to the published chart (https://valkey.io/valkey-helm/) once 1.0.0 is out.
- **A Valkey failover can lose the last second or so of writes.** Replication is asynchronous. A primary that can't reach any replica stops taking writes rather than take ones that would be lost.
- **KiviDB was considered and dropped.** v1.1.0 has replication, but no Sentinel, and Celery and Django can't use its cluster mode, so nothing would fail over. It can also lose the last few acknowledged writes when killed, e.g. by the OOM killer.
- **RustFS 1.0.x can't list a prefix containing `//`** ([rustfs/rustfs#8591](https://github.com/rustfs/rustfs/issues/8591)). The backup path has no trailing slash to avoid this. The fix isn't released yet.
- **The WAL archive and backups live in this cluster's RustFS**, and are copied elsewhere once a day by rclone, outside this repository. Anything since the last copy is only here.
- **WAL traffic to RustFS is plain HTTP** inside the namespace; it is encrypted at rest.
- **celery-exporter's image is amd64 only**, so it's pinned to amd64 nodes. Its last release (0.12.2) is from 2025-07 and ships Celery 5.5; the app runs 5.6, which speaks the same event protocol.
- **Kubelet certificates aren't verified** when Alloy scrapes them, as they're usually self-signed. The scrape still authenticates with Alloy's service account token.
- **Network policies cover only the `qsology` and `qsology-openbao` namespaces.** The operators', the gateway's and Alloy's namespaces are open. The policies haven't been run against a real cluster yet, so watch Hubble for drops on the first deployment (see [Network policies and Hubble](#network-policies-and-hubble)). The sites the app fetches from are listed by hostname; add any new one there.
- **Exports go out through the gateway.** The app uploads to RustFS through the public S3 hostname so the signed links match it.
