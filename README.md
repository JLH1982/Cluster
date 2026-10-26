# Pi cluster: rebuild from scripts

> **Public copy.** Passwords, keys and tokens are not in this repository. They live in an
> Ansible vault (`inventory/group_vars/all/vault.yml`, created by `playbooks/create-vault.yml`
> and git-ignored). A few identifying values are replaced with placeholders such as
> `your-onion-address-goes-here`.

Ansible playbooks that rebuild the whole Raspberry Pi k3s cluster, plus a backup of the app data
to restore into it. Every playbook is safe to re-run: it compares what's described here with what is
running and only changes the differences.

| Playbook | What it does |
|---|---|
| `playbooks/site.yml` | Everything below, in order (01 to 05) |
| `01-hosts.yml` | OS setup on all Pis: packages, kernel settings, IPv6 off, firewall (SSH only from the LAN and the Teleport VPN, 192.168.2.0/24), fail2ban (never bans your phone, laptop or tablet), SSH keys only (no passwords, no root), per-session sudo, journal kept on disk, no swap, Bluetooth/avahi/udisks2/rpcbind off, automatic updates with staggered reboots, Longhorn disks |
| `02-k3s.yml` | Installs k3s (master first, then node2/node3, then node1). Never touches a running node |
| `03-platform.yml` | MetalLB, Traefik, cert-manager, Longhorn, Prometheus/Grafana, Loki + Alloy (problem logs), CloudNativePG |
| `04-apps.yml` | Cloudflare tunnel, Authentik, portfolio site + visitor log, pool supply sample page (pool.hinkel-net.com), onion site, Immich, Home Assistant + Z-Wave, Uptime Kuma, homepage (www), UniFi Poller |
| `05-maintenance.yml` | Weekly etcd defrag, daily Immich patch-update and daily cloudflared update timers on the master |
| `backup.yml` | Copies app data to `backups/<date-time>/` on this laptop |
| `restore-data.yml` | Puts a backup's data back into a (re)built cluster |
| `create-vault.yml` | Reads the cluster's secrets into the encrypted vault |
| `new-sso-secrets.yml` | One time only: created the cert-manager and Authentik secrets (see below) |
| `new-visitor-log-secrets.yml` | One time only: created the visitor log's database passwords (see below) |

Where things are: settings are in `inventory/group_vars/all/vars.yml`, per-Pi settings are in
`inventory/host_vars/`, and manifests and Helm values are in `files/`.

## One-time setup

1. **Save the vault password.** `~/.ansible/cluster-vault-pass` unlocks
   `inventory/group_vars/all/vault.yml`, which holds every secret: the k3s token, the Cloudflare
   tunnel token, the onion address keys, database passwords and the Z-Wave security keys. Copy it
   into your password manager. Without it the vault can't be opened.
2. **Create the vault** (it asks for the `kube` sudo password):
   ```bash
   cd ~/cluster-ansible
   ansible-playbook playbooks/create-vault.yml
   ```
   Re-run it whenever a secret changes, for example a new Cloudflare tunnel token.

Before using any playbook in a new terminal, load your SSH key (it asks for the key passphrase once):
```bash
eval $(ssh-agent) && ssh-add
cd ~/cluster-ansible
```

## Everyday use

```bash
ansible-playbook playbooks/site.yml --check --diff   # preview: what would change?
ansible-playbook playbooks/site.yml                  # apply
ansible-playbook playbooks/backup.yml                # back up app data to this laptop
```

**Run `backup.yml` regularly**, for example weekly. A rebuild without a recent backup brings
back empty apps.

To change the cluster, edit the files here and run the playbook. If you make a change directly on
the cluster, copy it back into these files, or the next run will undo it.

## What a backup contains

`backups/<date-time>/` holds:
- `immich-db.dump`: the Immich database (albums, users, faces, metadata)
- `home-assistant.tgz`: the Home Assistant config
- `zwave-js-ui.tgz`: the Z-Wave network and settings
- `uptime-kuma.tgz`: Uptime Kuma's monitors and history
- `visitor-log-db.dump`: the portfolio visitor log
- `website.tgz` and `darkweb-site.tgz`: the two websites' files
- `etcd-snapshot.db`: every Kubernetes object, secrets included

**Not included: the Immich photo files themselves.** Those are copied off-site to AWS S3 every
night instead (see below).

## Photo backup to AWS S3

Every night at 3:30 AM, the `immich-s3-backup` CronJob (`files/apps/immich/03-s3-backup.yaml`) copies
Immich's whole data folder to the S3 bucket **`<your-immich-backup-bucket>`** (us-east-2), folder `immich-data/`:
original photos and videos (`upload/`), Immich's own daily database dumps (`backups/`) and thumbnails.
They're plain files, so you can browse and download them in the AWS console.

- **Versioned:** deleted or changed files stay recoverable for 90 days (bucket lifecycle rule).
- **Least privilege:** the job uses the IAM user `immich-s3-backup`, which can only read and write
  current files in this bucket. It can't delete old versions or change bucket settings.
- **Monitored:** after each successful run the job reports to the Uptime Kuma push monitor
  "Immich photo backup to S3"; if no run succeeds for 25 hours you get a phone alert.
- **Run it now:** `sudo kubectl -n immich create job --from=cronjob/immich-s3-backup immich-s3-backup-manual`
- **Restore:** `files/apps/immich/s3-restore-job.yaml` (used in REBUILD.md step 7).
- **The bucket and IAM user were created by hand** with the AWS CLI; they aren't managed by these
  playbooks. Their access key is in the `immich-s3-backup` secret and the vault.

The backup folder contains secrets, so keep it private, and don't commit it to git.

**Off-site copy:** at the end of each run, `backup.yml` packs this whole folder (scripts, encrypted
vault) with the new backup, encrypts it with the Ansible vault password (gpg, AES256) and uploads it
to S3 bucket `<your-cluster-backup-bucket>` (folder `cluster-ansible/`). The laptop's AWS profile `cluster-backup` (IAM user
`cluster-ansible-backup`) can only upload: it can't list, download or delete, so restores go through
the AWS console. Nothing expires automatically. See REBUILD.md "Laptop gone?".

## Database backups to S3

Every night at 2:15 AM the `db-backup` CronJob (`files/apps/db-backup.yaml`) dumps the **Authentik**
(users, MFA devices) and **visitor log** databases, encrypts them with rclone (passphrase
`vault_db_backup_passphrase` in the vault) and uploads them to the photo-backup bucket, folder
`db-dumps/` (`authentik.dump.bin`, `visitorlog.dump.bin`). Each night replaces the last; the bucket
keeps old versions 90 days, so any of the last 90 nights can be restored (AWS console > the file >
Versions). Immich's database isn't included: Immich dumps it itself and the photo backup copies it.
A failed or missed night alerts the phone. `backup.yml` still makes local backups as before.

- **Run it now:** `sudo kubectl -n db-backup create job --from=cronjob/db-backup db-backup-manual`
- **Restore:** download and decrypt on any machine with rclone, then load it with `restore-data.yml`:
  ```bash
  export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=...   # the photo-backup key (vault_immich_s3_backup)
  export RCLONE_CRYPT_REMOTE=":s3,provider=AWS,env_auth=true,region=us-east-2:<bucket>/db-dumps"
  export RCLONE_CRYPT_FILENAME_ENCRYPTION=off RCLONE_CRYPT_PASSWORD="$(rclone obscure '<vault_db_backup_passphrase>')"
  rclone copy :crypt:authentik.dump backups/from-s3/ && mv backups/from-s3/authentik.dump backups/from-s3/authentik-db.dump
  ansible-playbook playbooks/restore-data.yml -e backup=backups/from-s3 -e only=authentik-db -e confirm=yes
  ```
  (`visitorlog.dump` the same way, renamed `visitor-log-db.dump`, with `only=visitor-log-db`.)

## Home-network apps: names, HTTPS and login

The home-network apps are at `https://<app>.lan.hinkel-net.com`: `home`, `grafana`, `uptime`,
`longhorn`, `ha`, `zwave` and `auth` (Authentik). They only work at home or on the VPN: the public
DNS record `*.lan.hinkel-net.com` (DNS only, not proxied) points at Traefik's LAN address 10.0.20.230.

- **HTTPS:** cert-manager gets one Let's Encrypt wildcard certificate for `*.lan.hinkel-net.com`
  (`files/platform/lan-certificate.yaml`) and renews it by itself. It proves ownership with a
  temporary DNS record through a Cloudflare API token that can only edit hinkel-net.com DNS
  (`vault_cloudflare_dns_token`). Traefik uses it as its default certificate. http:// redirects to https://.
- **Login (Authentik):** `home`, `uptime`, `longhorn` and `zwave` ask for the Authentik login before
  the page loads (Traefik forward auth, `files/apps/authentik/03-forward-auth.yaml`); one login
  covers all of them. Grafana has a "Sign in with Authentik" button (OIDC). Home Assistant keeps its
  own login (its phone app doesn't work behind another login page).
  Which apps use Authentik is set in `files/apps/authentik/02-blueprint.yaml`; users and MFA are set
  in the Authentik admin UI and saved in its database (`authentik-db.dump` in each backup).
- **Not changed:** the public names (www, photos, ha, portfolio) still go through the Cloudflare tunnel,
  and the www login stays Cloudflare Access + Cognito.
- **If Authentik is down:** Longhorn and Z-Wave can go back to their old password login; the
  middleware to swap in is noted in their Ingress files. Grafana's `admin` login always works.

## Problem logs (Cluster Problems dashboard)

Grafana > Dashboards > **Cluster Problems** shows only what went wrong, for the last 30 days, with a
Node dropdown and a search box:
- **Summary:** errors, warnings, pod errors, reboots, container restarts and fail2ban bans.
- **Timeline:** problems per Pi (red markers = reboots), uptime, CPU temperature (Pi 5s throttle
  from 85 °C), and which containers restarted.
- **Logs:** hardware/power/disks (under-voltage, USB and disk resets, I/O errors, out of memory,
  boots and shutdowns), k3s and etcd, Kubernetes warning events and node not-ready/reboots,
  fail2ban bans and failed SSH logins, pod errors, and everything else from the Pis' journals.

How it works: **Alloy** runs on every Pi (`helm-values/alloy.yaml`) and reads the system journal
(kept on disk, `files/host/journald/`) and the pod logs, keeps only warnings and worse plus known
trouble signs, and sends them to **Loki** (`helm-values/loki.yaml`, one instance, 5Gi Longhorn volume,
never on node1). A second Alloy (`alloy-events.yaml`) sends Kubernetes events once for the cluster.
fail2ban writes to the journal (01-hosts.yml) so its bans are included, and a device banned on
one Pi is banned on all four within a minute (`fail2ban-cluster-sync` timer on each Pi, reading the
bans from Loki at the fixed IP `loki_service_ip`; `files/host/fail2ban/`). Reboots, uptime,
temperatures and restarts come from Prometheus. The logs aren't in `backup.yml`: they're only kept
30 days anyway.

After an unexpected reset, open "Hardware, power and disks" around the red reboot marker. A hard
reset often leaves nothing in the log just before it; if the last lines are normal and the next is
"Booting Linux", suspect power or heat rather than software.

## Visitor log and honeypot

Records who opens https://portfolio.hinkel-net.com (so you can see whether someone at a company you
applied to looked at it), what scanners probe for, and a honeypot. Grafana > Dashboards >
**Visitors & Honeypot** shows a world map (dots with the number of requests when more than one) and
tables; the **Source** dropdown switches between Portfolio visitors, Portfolio probes, Honeypot
(admin), www login page and Blocked by Cloudflare (several or All at once).

Where the data comes from (all in the `visitor-log` app, `files/apps/visitor-log/`, saved in its own
Postgres):
- **Portfolio:** nginx in the website pod sends a copy of each page view and each probe (things a
  static site doesn't have: `/.env`, `/wp-login.php`, `/phpmyadmin/`...) to the app.
- **Honeypot:** https://admin.hinkel-net.com is a fake admin login served by the app. It records
  every request, the usernames/passwords tried and anything else posted. Nothing behind it is real.
- **Cloudflare:** requests Cloudflare answered itself - the www login page (www is behind
  Cloudflare Access, so those never reach the cluster) and bots blocked by Bot Fight Mode on any
  host - read from Cloudflare's GraphQL API every 5 minutes. Needs the optional Analytics Read token
  (asked for by `new-visitor-log-secrets.yml`). Cloudflare samples these and gives a country, not a city.

Cloudflare supplies the visitor's IP (`CF-Connecting-IP`) and city/country/coordinates for requests
that reach the cluster; the location headers come from the zone setting Rules > Settings > Managed
Transforms > **Add visitor location headers** (turned on 2026-10-25). The app also looks up the
reverse-DNS name (domain) and the owner of the IP block (RDAP: a company, university or ISP).

Set in the Cloudflare dashboard, not by these scripts: that setting, and the admin.hinkel-net.com
tunnel route and DNS record (added 2026-10-26), and the same for pool.hinkel-net.com. Also there: the bare domain hinkel-net.com goes to
the portfolio (Rules > Redirect Rules > "Bare domain to the portfolio", 301, keeps the path), and
the block list: account IP list `honeypot_attackers` (Manage Account > Configurations > Lists) and the
zone rule Security > WAF > Custom rules > "Block honeypot attackers" (added 2026-10-26).

- **IP addresses** (and the full reverse-DNS name, which often contains the IP) are deleted after
  **7 days** for visitors (portfolio pages, www) and **90 days** for probes and the honeypot.
  Everything else is kept. Change `KEEP_IP_DAYS` / `KEEP_ATTACKER_IP_DAYS` in `02-logger.yaml`.
- **Bots** on the portfolio (crawlers, link previews, Uptime Kuma) are flagged from the browser name
  and hidden by default ("Bots on the portfolio" switch).
- A company only shows up by name when the visitor is on the company's own network (office or VPN).
  From home or a phone you'll see the ISP (Comcast, Verizon...) and the city.
- Don't type a real password into admin.hinkel-net.com: it is stored in plain text like every other attempt.
- **Cloudflare block list:** every 6 hours the app puts IPs that tried a password on the honeypot,
  or asked for 3+ different probe paths, from the last 30 days into the `honeypot_attackers` list. The
  rule "Block honeypot attackers" blocks them on every hostname except admin (so the honeypot keeps
  recording them). Never listed: your home IP (so you can try the honeypot), and home/mobile
  providers (Comcast, Verizon, T-Mobile...: shared IPs a recruiter could be on). IPv6 is listed per
  /64. The list is replaced each time, so IPs drop off by themselves. Needs the Cloudflare token's
  Account Filter Lists > Edit permission. To unblock someone, turn the rule off in the dashboard.
- **Phone notification** (ntfy, same topic as the alerts) when a portfolio page is opened from a
  network that isn't a home/mobile provider, cloud host or VPN - so probably a company, school or
  agency - at most once a day per organisation. Put the companies you applied to in `WATCH_ORGS` in
  `02-logger.yaml` (a regex, e.g. `target|best buy|medtronic`): their visits come with high priority
  and a star. The skip list is `NOT_A_COMPANY` in the script. Microsoft, Google and Apple aren't
  skipped (their staff could be visiting), so some of their cloud bots may get through.

## Alerts

Prometheus alerts (critical and warning) go to your phone through ntfy, on the same topic Uptime Kuma
uses. The topic URL is in the vault (`vault_alertmanager_ntfy`) and becomes the `alertmanager-ntfy`
Secret. Routing is under `alertmanager.config` in `files/helm-values/prometheus.yaml`; a few alerts
that are normal on busy Pis with USB disks (disk/system saturation, CPU throttling) are muted there.
Custom rules are in `files/platform/k3s-alerts.yaml`, including `PrometheusDiskFilling` (over 80%)
and `PrometheusNotSavingData`. All alerts, muted or not, are listed in Grafana > Alerting > Alert rules.

Also sent to the phone:
- **A Pi restarted** (`PiRebooted`, k3s-alerts.yaml) - muted 02:55-05:00 Central, when the nightly
  updates may reboot each Pi at its `reboot_time` (Alertmanager time interval `nightly-updates`).
- **From the logs** (`files/platform/log-alerts.yaml`, evaluated by Loki): under-voltage, disk or
  USB errors, out-of-memory kills, and fail2ban bans (with the banned IP).
- **Database backup failed or older than a day** (`DatabaseBackupFailed`, `DatabaseBackupStale`).
- **Someone on a company's network opened the portfolio** - sent by the visitor log itself, at most
  once a day per company; see "Visitor log and honeypot".

## Disaster recovery

**Starting over on fresh Pis? Follow [REBUILD.md](REBUILD.md) step by step.** The sections below
cover smaller repairs.

### A Pi died and is replaced (or re-flashed)
1. Flash **Raspberry Pi OS Lite 64-bit (Debian 13)** with Raspberry Pi Imager. Use the same
   hostname, user `kube`, the same password, and your SSH key.
2. On the router, point the node's fixed-IP reservation at the new Pi's MAC address
   (UniFi: **Client Devices → the Pi → Settings → Fixed IP**).
3. Remove the old node from the cluster (run on the master):
   `sudo kubectl delete node <name>`.
   For node2 or node3 (etcd members), also run `sudo k3s-etcdctl member list` and then
   `sudo k3s-etcdctl member remove <id>` for the dead member.
4. Rebuild just that node:
   ```bash
   ansible-playbook playbooks/01-hosts.yml playbooks/02-k3s.yml --limit <name>
   ```
5. If it's node1 or the master, its Longhorn disk comes back automatically and Longhorn re-copies
   the replicas.

**If the master itself died** but node2 and node3 are fine: in `inventory/hosts.yml`, change the
`k3s_init` group to `node2`, then run step 4 for the master. It joins through node2. Change the group
back afterwards.

### Rebuild everything from scratch
1. Flash all four Pis (step 1 above) and check the router reservations still match.
2. Format the Longhorn USB disks if they're new: `sudo mkfs.ext4 -L longhorn /dev/sdX`, and update
   `longhorn_disk.src` in `host_vars/` if needed.
3. Run:
   ```bash
   ansible-playbook playbooks/site.yml
   ansible-playbook playbooks/restore-data.yml -e backup=backups/<newest> -e confirm=yes
   ```
4. Restore the Immich photos from S3 (REBUILD.md step 7).
5. The Cloudflare tunnel reconnects by itself: the token is in the vault and the routes live in
   Cloudflare. The onion address stays the same because its keys are in the vault.

### Undo a mistake inside the cluster (etcd snapshot)
This rolls back **every** Kubernetes object to the time of the snapshot. Your data on volumes is
not affected.
```bash
# on the master, with k3s stopped on all servers:
sudo k3s server --cluster-reset --cluster-reset-restore-path=/path/to/etcd-snapshot.db
```
See https://docs.k3s.io/datastore/backup-restore before doing this.

## Not covered by these scripts
- The **Immich photo files** - backed up separately to AWS S3 (see above).
- The **Cloudflare dashboard** (tunnel routes and DNS) and the **UniFi router**. Both keep their own
  backups in the cloud.
- The **www.hinkel-net.com login**: Cloudflare Access app "Homepage" (team `<your-team>`) using the
  "AWS Cognito (MFA)" login method, backed by the Cognito user pool `hinkel-net-access`
  (`<cognito-user-pool-id>`, us-east-2), which requires an authenticator app code. All of it lives in
  Cloudflare and AWS, so a cluster rebuild doesn't affect it.
- **Grafana and Uptime Kuma logins.** After a full rebuild, Grafana gets a new
  admin password. Uptime Kuma's login comes back from its backup.
- The **tor-onion image**. It's on ghcr.io; `files/images/tor-onion/Dockerfile` rebuilds it if it
  ever disappears.
