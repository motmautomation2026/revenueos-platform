# Deploying RevenueOS on the Hostinger VPS

Two containers from `docker-compose.yml`, built on the VPS:

| Container | What it runs | Reachable from |
|---|---|---|
| `app` | the Next.js web app | `127.0.0.1:3000` only; the VPS's existing Caddy serves **https://revenueos.b2botix.ai** |
| `worker` | the Brain worker, `python run_for.py work` | nothing (outbound calls only) |

Supabase, OpenAI, QuickICP/Prospeo, the email tool and Quickhowl stay where they are; both
containers only call out to them. Secrets live only on the VPS in `.env.local` and `brain/.env`.

> **Only one worker at a time.** Two workers on the same database would run the same customer's
> steps twice. Stop the Windows worker before the VPS one starts (step 7).

---

## First time

### 1. DNS
At the DNS provider of `b2botix.ai`, add an **A record**: `revenueos` → the VPS's IP address.
Check from any machine: `nslookup revenueos.b2botix.ai` shows the VPS IP.

### 2. What the VPS already has
```bash
docker --version && docker compose version     # Docker + Compose v2
systemctl is-active caddy                       # "active" = Caddy installed on the server
docker ps --format '{{.Names}} {{.Image}}' | grep -i caddy   # a line here = Caddy runs in Docker
free -h                                         # the app build needs ~2 GB free memory
```
No Docker: `curl -fsSL https://get.docker.com | sh`.
Less than 2 GB free: add swap once (`fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile`,
plus `/swapfile none swap sw 0 0` in `/etc/fstab`).

### 3. The code
```bash
sudo mkdir -p /opt/revenueos && sudo chown "$USER" /opt/revenueos
git clone https://github.com/motmautomation2026/revenueos-platform.git /opt/revenueos
cd /opt/revenueos
```
The repository is private: use a GitHub **deploy key** (read-only) or a personal access token when git asks.

### 4. The secrets (from this Windows PC)
```powershell
cd "D:\MOTM Diagnose\RevenueOS\revenueos-platform"
scp .env.local root@<VPS-IP>:/opt/revenueos/.env.local
scp brain\.env root@<VPS-IP>:/opt/revenueos/brain/.env
```
On the VPS:
```bash
chmod 600 .env.local brain/.env
ln -s .env.local .env      # Docker Compose reads its settings from .env
```

### 5. Build and start the app only
```bash
chmod +x deploy/*.sh
docker compose up -d --build app
curl -sI http://127.0.0.1:3000/login | head -1      # HTTP/1.1 200 OK
```
The first build takes a few minutes.

### 6. Caddy
Add the block from `deploy/Caddyfile.snippet` to the existing Caddyfile and reload Caddy
(`sudo systemctl reload caddy`, or restart the Caddy container). Caddy gets the HTTPS
certificate by itself. Open **https://revenueos.b2botix.ai** and sign in.

If Caddy runs **in Docker**: use `reverse_proxy 172.17.0.1:3000` in the block, add
`APP_BIND=172.17.0.1:3000` to `.env.local`, and run step 5 again.

**Supabase:** in the Supabase dashboard → Authentication → URL Configuration, set the
**Site URL** to `https://revenueos.b2botix.ai` and add it to **Redirect URLs**, so sign-in and
password e-mails point to the live site.

### 7. Move the worker (the cut-over)
On **Windows** (PowerShell as the same user), stop the old worker for good and pack the customer folders:
```powershell
Stop-ScheduledTask -TaskName 'RevenueOS Brain Worker'
Disable-ScheduledTask -TaskName 'RevenueOS Brain Worker'
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'run_for.py work' }   # must show nothing
cd "D:\MOTM Diagnose\RevenueOS\revenueos-platform\brain"
tar -czf customers.tgz customers
scp customers.tgz root@<VPS-IP>:/opt/revenueos/customers.tgz
```
On the **VPS**, load them into the worker's volume, then start the worker:
```bash
cd /opt/revenueos
docker compose create worker                          # makes the volume with the right owner
docker run --rm -v revenueos_brain-customers:/to -v "$PWD":/from alpine \
  sh -c 'cd /to && tar xzf /from/customers.tgz --strip-components=1 && chown -R 1000:1000 /to'
rm customers.tgz
./deploy/deploy.sh --no-pull
docker compose logs -f worker      # "Worker 'revenueos-worker' started. Looking every 30 seconds."
```
Any run that was waiting (a decision, a Start card) carries on from where it was.

---

## Every day

| Task | Command (in `/opt/revenueos`) |
|---|---|
| Update to the latest `main` | `./deploy/deploy.sh` |
| Worker log (what the Windows `logs/worker.log` showed) | `docker compose logs -f worker` |
| One customer's step logs | `docker compose exec worker ls customers/<key>/logs` then `... cat customers/<key>/logs/<file>` |
| App log | `docker compose logs -f app` |
| Restart the worker | `docker compose restart worker` |
| State of both | `docker compose ps` |
| Back up customer folders | `./deploy/backup.sh` (into `./backups/`) |

A nightly backup: `crontab -e` and add
`30 2 * * * cd /opt/revenueos && ./deploy/backup.sh >/dev/null 2>&1`.

Changing a secret: edit `.env.local` or `brain/.env`, then `./deploy/deploy.sh --no-pull`.
