# Monitoring for one box

Grafana graphs of one Linux machine's CPU, load, memory, disk, network, temperatures
and GPU, kept for 30 days (you can change that). Four containers started by one
`docker compose up -d`:

| Service | What it does |
|---|---|
| `node-exporter` | Reads the machine's own sensors: CPU, memory, disk, network, temperatures (hwmon) and, with `--collector.drm`, AMD GPU busy percent and memory |
| `nvidia-gpu-exporter` | NVIDIA machines only (compose profile `nvidia`): GPU load, memory, temperature and power through `nvidia-smi` |
| `prometheus` | Asks the exporters for their numbers every 15 seconds and stores them |
| `grafana` | The web page that draws them. One dashboard, "This box", is ready |

It binds to `127.0.0.1` by default, so nothing is reachable from the network until you
decide it should be. Images are pinned to exact versions, built for both amd64 (x86 PCs,
AMD Strix Halo) and arm64 (for example NVIDIA DGX Spark).

**Not tested on AMD Strix Halo.** The AMD GPU panels use node-exporter's `drm`
collector, whose metric names were checked in its source, but no one has yet seen them
fill with data on a Strix Halo machine. There is also a known amdgpu kernel bug where
`gpu_busy_percent` sticks at 100 on some older integrated GPUs
([drm/amd issue 2448](https://gitlab.freedesktop.org/drm/amd/-/issues/2448)); if the
GPU busy panel sits at 100 while the machine is idle, that bug is the likely cause, not
this stack. Step 5 has a one-line check you can run before you look at the dashboard.

## 1. Install Docker with the compose plugin

Follow [docs.docker.com/engine/install](https://docs.docker.com/engine/install/) for
your distribution (it includes the `docker compose` plugin). Add yourself to the
`docker` group (`sudo usermod -aG docker $USER`), log out and in again.

Check: `docker compose version` prints a version, and `docker ps` runs without `sudo`.

## 2. NVIDIA machines only: install the NVIDIA Container Toolkit

Skip this on AMD or GPU-less machines. Follow NVIDIA's
[install guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
(the NVIDIA driver must already work: `nvidia-smi` prints your GPU), then register it
with Docker:

```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

Check: `docker info | grep -i runtimes` lists `nvidia`.

## 3. Get this folder and write your settings

Clone this repo (the lines below assume `~/dev/devbox-kit`), then:

```bash
cd ~/dev/devbox-kit/monitoring
cp .env.example .env
```

Edit `.env` (every setting is explained in the file). You must set two things:

- `RUN_AS`: the containers store data in `./data` and read this folder as you, so give
  them your numeric user and group. Print them with `printf '%s:%s\n' "$(id -u)" "$(id -g)"`.
- `GRAFANA_ADMIN_PASSWORD`: the login password for Grafana (user `admin`). There is no
  default: the stack refuses to start without one. Anonymous access is switched off.

On an NVIDIA machine also set `COMPOSE_PROFILES=nvidia`, and copy the file that tells
Prometheus to scrape the NVIDIA exporter:

```bash
cp targets/nvidia.yml.example targets/nvidia.yml
```

Check: `docker compose config --quiet` prints nothing. If it prints
`required variable ... is missing a value`, that setting is still empty in `.env`.

## 4. Start it

```bash
docker compose up -d
```

The first start downloads the images. Check: `docker compose ps` lists every service as
`running` (three services, four on NVIDIA), and
`curl -s localhost:9090/-/healthy` prints `Prometheus Server is Healthy.`
(use your `PROMETHEUS_PORT` if you changed it).

## 5. Check the numbers are arriving

All machines: `curl -s localhost:9090/api/v1/targets | grep -o '"health":"[a-z]*"'`
prints `"health":"up"` once per target (two targets, three on NVIDIA).

AMD machines: `curl -s localhost:9100/metrics | grep node_drm_gpu_busy_percent` prints a
line per GPU such as `node_drm_gpu_busy_percent{card="card1"} 3`. (Use your
`NODE_EXPORTER_PORT` if you changed it.) No line means the kernel driver does not offer
the file `/sys/class/drm/card*/device/gpu_busy_percent`; the AMD panels then stay empty
while everything else works.

NVIDIA machines: `curl -s localhost:9835/metrics | grep nvidia_smi_utilization_gpu_ratio`
prints one line per GPU.

## 6. Open Grafana

Grafana listens on `127.0.0.1` of the box, so from your laptop forward the port over ssh
(`mybox` is the ssh alias of the machine; see [`../session/README.md`](../session/README.md)
step 2 for setting one up):

```bash
ssh -L 3000:localhost:3000 mybox
```

Then open <http://localhost:3000> in the laptop's browser and sign in as `admin` with the
password from `.env`. On the box itself you can open the same address directly.

Check: after signing in, Dashboards > Devbox > **This box** shows graphs with data
(give it a minute after the first start). The "Machine" selector at the top lists the
machines being monitored. The GPU row for the vendor you do not have stays empty; that
is expected. NVIDIA GPUs that share memory with the CPU (the DGX Spark) report no separate
GPU memory, so the "GPU memory used" panel is empty there too.

## Changing things

Everything is a file in this folder; there is no web setup to redo.

**Add another machine.** Run node-exporter on it (the same `docker-compose.yml` works:
put `RUN_AS`, any value for `GRAFANA_ADMIN_PASSWORD`, and `EXPORTER_BIND_ADDRESS` set to
that machine's private or VPN address in its `.env`, then
`docker compose up -d node-exporter`; only machines you trust should be able to reach that
address). Then, on the monitoring box, copy `targets/example.yml.example` to
`targets/other-machine.yml`, put in the address and the name you want in the `instance`
label, and save. Prometheus picks the file up within seconds, no restart. Check:
`curl -s localhost:9090/api/v1/targets | grep other-machine` shows it with `"health":"up"`.
Give every exporter of one machine the same `instance` name so the dashboard shows its
CPU and GPU together. Any program that speaks the Prometheus format can be scraped this way.

**Alerts.** Copy `rules/example.yml.example` to `rules/my-alerts.yml`, edit, then
`docker compose restart prometheus`. Check: `curl -s localhost:9090/api/v1/rules | grep -o '"type":"alerting"'`
prints one line per alert rule. Firing alerts show on Prometheus' Alerts page and in Grafana (Alerting > Alert
rules). This stack sends no e-mail or message; that needs an Alertmanager, which is not
included.

**More dashboards.** Save a dashboard's JSON (Grafana: Share > Export) into `dashboards/`.
It appears under Dashboards > Devbox within 30 seconds. Dashboards are read-only in the
page: edit the file, not the web copy. The data source has the fixed id
`devbox-prometheus`, so exported JSON should refer to that.

**Keep data for longer or shorter.** Change `RETENTION` in `.env`, then `docker compose up -d`.

**Reach Grafana from other machines without a tunnel.** Set `BIND_ADDRESS` in `.env` to
the box's LAN or VPN address (or `0.0.0.0` for every network), then
`docker compose up -d`. Warning: Prometheus, which has no login at all, listens on that
address too, and Grafana is protected only by its one password over plain HTTP. Do this
only on a network you trust, or put a firewall in front of both ports.
Check: from the other machine, `curl -s http://<box-address>:3000/api/health` prints
`"database": "ok"`.

**Change a port.** Set it in `.env` and run `docker compose up -d`. If another
program already uses port 9100 (a node-exporter installed from a package, for example),
set `NODE_EXPORTER_PORT` to something else; the same goes for 9835 and
`NVIDIA_EXPORTER_PORT` (and edit the port in `targets/nvidia.yml` to match).

## Stopping, removing, where the data lives

- `docker compose stop` / `docker compose start`: pause and resume.
- `docker compose down`: remove the containers; your data and settings stay.
- Stored numbers live in `data/prometheus`, Grafana's own state (users, preferences) in
  `data/grafana`. To wipe them, run `docker compose down` and delete everything in those
  two folders except the `.gitkeep` files.
- Containers come back after a reboot by themselves (`restart: unless-stopped`).

Check: after `down`, `docker compose ps` lists nothing; `ls data/prometheus` still lists
files.

## Notes

- Every container carries the label `lifecycle=service`.
- `GRAFANA_ADMIN_PASSWORD` is passed to Grafana as an environment variable, so anyone who
  can run `docker inspect` on the box can read it. Keep `.env` private (`chmod 600 .env`).
- The services use the host network so node-exporter sees the real network interfaces
  (its upstream README recommends this) and so the ports in `.env` are the real ports.
- Everything is a plain Prometheus and Grafana setup; the dashboards are ordinary
  Grafana JSON, so they also work in any other Grafana that has a Prometheus data source
  with the id `devbox-prometheus`.
