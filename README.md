# fleet-monitoring

Grafana dashboards and alerts for one Linux machine or many: CPU, memory, disks, network,
temperatures, NVIDIA and AMD GPUs, disk health, containers, logs, and checks on websites
and ports. Everything runs in Docker from one compose file. Each machine picks the parts
it runs; everything specific to your machines lives in one folder of your own, so this
repo never needs editing and can be updated by pulling.

| Part (role) | What it does | Where it usually runs |
|---|---|---|
| `hub` | Prometheus: collects numbers from every machine every 15 s and stores them | one machine |
| `viewer` | Grafana: the dashboards | one machine (same as the hub, or another) |
| `agent` | node-exporter: CPU, memory, disk, network, temperatures, AMD GPU | every machine |
| `nvidia` | NVIDIA GPU load, memory, temperature, power | machines with an NVIDIA GPU |
| `alerting` | Alertmanager: sends firing alerts to e-mail, chat, a webhook | one machine |
| `probes` | blackbox exporter: is this website or port answering? | one machine |
| `cadvisor` | CPU, memory and network per container | machines running containers |
| `smartctl` | disk health (SMART), disk temperature, SSD wear | every machine with disks you care about |
| `logs` | Loki: stores logs | one machine |
| `logship` | Alloy: sends a machine's container and system logs to Loki | every machine whose logs you want |

Ready-made dashboards (Grafana folder "Fleet"): **This box** (system and GPU),
**Disks**, **Containers**, **Logs**. Starter alerts: a machine stops answering or
disappears, a disk is nearly full, failing or hot, a sensor or NVIDIA GPU is hot.

By default everything listens on `127.0.0.1` only: nothing is reachable from the network
until you decide it should be. Images are pinned to exact versions built for both amd64
(x86 PCs, AMD Strix Halo) and arm64 (for example NVIDIA DGX Spark).

**Not yet tried on AMD Strix Halo.** The AMD GPU panels use node-exporter's `drm`
collector (metric names checked in its source). There is a known amdgpu kernel bug where
`gpu_busy_percent` sticks at 100 on some older integrated GPUs
([drm/amd issue 2448](https://gitlab.freedesktop.org/drm/amd/-/issues/2448)); if the GPU
busy panel sits at 100 on an idle machine, that bug is the likely cause.

## How it is laid out

```
fleet-monitoring/            this repo (the engine): never edit, update with git pull
  docker-compose.yml         every part, switched on by COMPOSE_PROFILES
  dashboards/  rules/        the shipped dashboards and starter alerts
  check.sh                   health check
  site.example/              a complete example of YOUR folder
  .env                       yours (from .env.example): which parts, ports, paths
  site/                      yours (from site.example): targets, alerts, dashboards, passwords
  data/                      stored numbers, logs and Grafana state
```

`.env`, `site/` and `data/` are never committed. To keep your site in a git repo of your
own, put it anywhere and point `SITE_DIR` in `.env` at it.

## Quick start: one machine

### 1. Install Docker with the compose plugin

Follow [docs.docker.com/engine/install](https://docs.docker.com/engine/install/) for your
distribution (it includes `docker compose`; tested with versions 2.29 and 5.2). Add yourself to the
`docker` group (`sudo usermod -aG docker $USER`), log out and in again.

Check: `docker compose version` prints a version, and `docker ps` runs without `sudo`.

### 2. NVIDIA machines only: install the NVIDIA Container Toolkit

Skip this on AMD or GPU-less machines. Follow NVIDIA's
[install guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
(the driver must already work: `nvidia-smi` prints your GPU), then:

```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

Check: `docker info | grep -i runtimes` lists `nvidia`.

### 3. Get the repo and make your own settings and site folder

```bash
git clone https://github.com/farhanferoz/fleet-monitoring ~/dev/fleet-monitoring
cd ~/dev/fleet-monitoring
cp .env.example .env
cp -r site.example site
chmod 700 site/secrets
(umask 077; printf '%s' 'choose-a-password' > site/secrets/grafana_admin_password)
```

Open `.env` (every setting is explained in it) and check `RUN_AS`: the containers store
data in `./data` and read `site/` as you, so it must be your numeric user and group,
printed by `printf '%s:%s\n' "$(id -u)" "$(id -g)"`. The default `COMPOSE_PROFILES`
(`hub,viewer,agent`) runs everything a single machine needs.

On an NVIDIA machine also add `,nvidia` to `COMPOSE_PROFILES` and
`cp site/targets/nvidia.yml.example site/targets/nvidia.yml`.

Check: `docker compose config --quiet` prints nothing.

### 4. Start it and check it

```bash
docker compose up -d
./check.sh
```

The first start downloads the images. Check: `./check.sh` prints only `PASS` and `SKIP`
lines (SKIP = a part this machine does not run). Give it half a minute after the first
start; a target that has not been collected yet shows as not up.

AMD machines: `curl -s localhost:9100/metrics | grep node_drm_gpu_busy_percent` prints a
line per GPU such as `node_drm_gpu_busy_percent{card="card1"} 3`. No line means the
kernel driver does not offer `/sys/class/drm/card*/device/gpu_busy_percent`; the AMD
panels then stay empty while everything else works.

### 5. Open Grafana

Grafana listens on `127.0.0.1` of the machine, so from your laptop forward the port over
ssh (`mybox` is the machine's ssh alias; see
[devbox-kit's session guide](https://github.com/farhanferoz/devbox-kit/blob/main/session/README.md)
for setting one up):

```bash
ssh -L 3000:localhost:3000 mybox
```

Open <http://localhost:3000> and sign in as `admin` with the password from
`site/secrets/grafana_admin_password`. Check: Dashboards > Fleet > **This box** shows
graphs. The GPU row for the vendor you do not have stays empty; that is expected. NVIDIA
GPUs that share memory with the CPU (the DGX Spark) report no separate GPU memory.

## Several machines

One machine runs the `hub` (and usually `viewer` and `alerting`); every other machine
runs only the parts that read it.

**On each further machine:** steps 1 to 3 above, then in its `.env` set
`COMPOSE_PROFILES=agent` (add `,nvidia`, `,smartctl`, `,cadvisor`, `,logship` as wanted)
and `EXPORTER_BIND_ADDRESS` to that machine's private or VPN address (a Tailscale address
works), so the hub can reach it. It needs no Grafana password. `docker compose up -d`.
Only machines you trust should be able to reach that address.

**On the hub:** add the machine to a targets file, e.g. `site/targets/workstation.yml`:

```yaml
- targets: ["100.64.0.12:9100"]
  labels: {job: node, instance: workstation}
- targets: ["100.64.0.12:9633"]
  labels: {job: smartctl, instance: workstation}
```

Prometheus picks the file up within seconds, no restart. Give every exporter of one
machine the same `instance` name so the dashboards show it as one machine. Check:
`./check.sh` on the hub lists the new targets as up, and the "Machine" selector in
Grafana offers `workstation`. Machines that sleep or are switched off show as down
while they are off, and the `TargetDown` alert fires after 5 minutes of that.

**Grafana on a different machine from Prometheus:** run `viewer` there with
`PROMETHEUS_URL=http://<hub address>:9090` in its `.env`, and give the hub
`BIND_ADDRESS` = its private or VPN address so Grafana can reach it.

**Machines that run node-exporter without Docker** (a package or a systemd service) work
the same: list their address in a targets file.

## Optional parts

Each is one entry in `COMPOSE_PROFILES` plus, usually, copying its example file. Run
`docker compose up -d` after changing `.env`, then `./check.sh`.

- **Alerts sent somewhere (`alerting`).** Edit `site/alertmanager.yml` (examples for a
  webhook and e-mail inside; keep tokens in `site/secrets/`), then
  `cp site/alertmanagers/local.yml.example site/alertmanagers/local.yml` so Prometheus
  sends to it. Without Alertmanager, firing alerts still show on Prometheus' Alerts page
  and in Grafana (Alerting > Alert rules). Check: `./check.sh` shows `PASS alert-path`.
- **Website and port checks (`probes`).** `cp site/scrape.d/probes.yml.example site/scrape.d/probes.yml`,
  list your addresses, `docker compose restart prometheus`. Probe types are in
  `site/blackbox.yml`. Result: `probe_success` (1 = answering).
- **Containers (`cadvisor`), disk health (`smartctl`).** Add the role and the matching
  lines of `site/targets/addons.yml.example` to a targets file. smartctl runs privileged
  (it reads the disks directly). Disks behind a USB bridge or a NAS's SCSI layer may
  need an explicit type; see the comment above `smartctl-exporter` in `docker-compose.yml`.
- **Logs (`logs` and `logship`).** On the machine that stores logs add `logs`; on every
  machine whose logs you want add `logship`, with `LOKI_URL` pointing at the Loki machine
  and `HOST_LABEL` = its name. On the Grafana machine,
  `cp site/grafana/datasources/loki.yml.example site/grafana/datasources/loki.yml`.
  Check: Dashboards > Fleet > **Logs** shows lines.

## Your own alerts and dashboards

- **Alerts.** Put rule files in `site/rules/` (start from `site/rules/example.yml.example`),
  then `docker compose restart prometheus`. Tests for them go in `site/rules/tests/`
  ([promtool's format](https://prometheus.io/docs/prometheus/latest/configuration/unit_testing_rules/));
  `./check.sh` runs them. The engine's starter alerts are in `rules/`; to change one, copy
  it into `site/rules/`, edit it there, and remove `/etc/prometheus/engine-rules/*.yml`
  from `site/prometheus.yml`.
- **Jobs that need more than an address** (a password, another path): one file per job in
  `site/scrape.d/` (see `site/scrape.d/example.yml.example`). Passwords go in files in
  `site/secrets/`, read with `password_file`.
- **Dashboards.** Save a dashboard's JSON (Grafana: Share > Export) into
  `site/dashboards/`; it appears in the "Site" folder within 30 seconds. Dashboards are
  read-only in the page: edit the file. Data source ids: `fleet-prometheus`, `fleet-loki`.
- **A login on Prometheus itself.** See `site.example/web.yml.example`.

## Changing the engine for one site

Do not edit `docker-compose.yml`. Write your own compose file beside your site that
includes the engine plus an override (Docker Compose's `include`; each file keeps its own
folder for relative paths):

```yaml
# my-site/compose.yml
include:
  - path: [/path/to/fleet-monitoring/docker-compose.yml, ./override.yml]
    env_file: ./.env
services:
  my-exporter:            # services only you need
    image: example/exporter:1.2.3
    labels: {lifecycle: service}
```

```yaml
# my-site/override.yml: changes to engine services, merged over the engine's definitions
services:
  prometheus:
    mem_limit: 768m
```

Then `docker compose -f my-site/compose.yml up -d`, and
`CHECK_ENV_FILE=my-site/.env ./check.sh -f my-site/compose.yml` for the health check.
To move an engine service off the host network, `network_mode: !reset null` plus
`networks:` and `ports:` in the override.

## Reaching Grafana without a tunnel

Set `BIND_ADDRESS` in `.env` to the machine's LAN or VPN address (or `0.0.0.0` for every
network), then `docker compose up -d`. Prometheus and Alertmanager then listen there too:
Prometheus has no login unless you add one (`web.yml`), and Grafana is protected by its
password over plain HTTP. Do this only on a network you trust, or put a reverse proxy
with TLS in front. Check: from another machine, `curl -s http://<address>:3000/api/health`
prints `"database": "ok"`.

## Updating, stopping, removing

- **Update:** `git pull`, then `docker compose up -d` and `./check.sh`. Your `.env`, `site/`
  and `data/` are untouched. New settings appear in `.env.example` and `site.example/`;
  compare with yours.
- `docker compose stop` / `docker compose start`: pause and resume.
- `docker compose down`: remove the containers; data and settings stay.
- Data lives in `data/` (Prometheus, Grafana, Alertmanager, Loki) and in a Docker volume
  for Alloy's position in the logs. To wipe it, `docker compose down -v` and empty the
  folders under `data/` (keep the `.gitkeep` files).
- Containers come back by themselves after a reboot (`restart: unless-stopped`).

## Notes

- Every container carries the label `lifecycle=service`.
- Grafana's admin password is read from a file, so it does not show in `docker inspect`.
- Services use the host network so node-exporter sees the real network interfaces (its
  upstream README recommends this) and the ports in `.env` are the real ports.
- The dashboards are generated by `tools/gen_dashboards.py`; edit that, not the JSON.
- Everything is plain Prometheus, Alertmanager, Grafana and Loki configuration; nothing
  here is needed to read it.
