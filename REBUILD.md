# Rebuild the cluster on fresh Pis

Step-by-step, from four Raspberry Pis with a freshly installed OS to the full cluster with your
apps and data back. Allow about 2 hours, most of it waiting.

**Your setup:** four Raspberry Pi 5s booting from NVMe drives, on Ethernet, with fixed IPs from the
UniFi router:

| Pi | IP | Role | Extra hardware |
|---|---|---|---|
| master | 10.0.20.240 | k3s server (first), Longhorn disk | 1.8 TB USB disk labeled `longhorn` |
| node1 | 10.0.20.241 | k3s agent, Longhorn disk | 1.8 TB USB disk labeled `longhorn` |
| node2 | 10.0.20.242 | k3s server | |
| node3 | 10.0.20.243 | k3s server | Nabu Casa ZWA-2 Z-Wave stick |

---

## Before you start: what you need

1. **This folder (`~/cluster-ansible`) including `inventory/group_vars/all/vault.yml`.**
   The vault file is *not* on GitHub. If this laptop is gone, download the newest encrypted copy
   from S3 first (see "Laptop gone?" below).
2. **The Ansible vault password** (Edge passwords: "Ansible vault password").
3. **A recent app-data backup**: a folder under `backups/` from `playbooks/backup.yml`.
4. **The Pi password** (Edge: "Pi cluster SSH/sudo") and your laptop SSH key passphrase.

> **Photos:** the Immich photo library is copied to AWS S3 every night (bucket `<your-immich-backup-bucket>`), and
> step 7 restores it from there. Before wiping the USB disks, check the last backup ran: Uptime Kuma
> "Immich photo backup to S3" is green, or the AWS console shows files in `immich-data/upload/`
> from the last day or two. Anything uploaded after the last nightly copy exists only on those disks.

---

## Step 1 - Install the OS on each Pi (Raspberry Pi Imager)

Do this for each of the four Pis.

1. On Windows, open **Raspberry Pi Imager**.
   - **Device:** Raspberry Pi 5
   - **OS:** Raspberry Pi OS (other) → **Raspberry Pi OS Lite (64-bit)** (Debian 13 "trixie")
   - **Storage:** the Pi's NVMe drive in a USB-NVMe adapter. (Or write to an SD card first and
     copy to NVMe on the Pi with Imager / SD Card Copier.)
2. Click **Next → Edit settings**:
   - **Hostname:** `master`, `node1`, `node2` or `node3`
   - **Username:** `kube` · **Password:** the Pi password from Edge
   - **Wireless LAN:** leave off (the cluster uses Ethernet)
   - **Locale:** time zone `America/Chicago`
   - **Services tab:** enable SSH → **Allow public-key authentication only** → paste the
     laptop's key (in Ubuntu: `cat ~/.ssh/id_ed25519.pub`)
3. Write the drive, fit it in the Pi, connect Ethernet (and the USB disk / Z-Wave stick per the
   table above), and power on.
4. **If a Pi doesn't boot from NVMe:** boot it once from an SD card and run
   `sudo raspi-config` → **Advanced Options → Boot Order → NVMe/USB Boot**, then reboot.

## Step 2 - Point the router's fixed IPs at the new Pis

New Pis have new network cards, so the router's reservations must be updated.

1. UniFi → **Client Devices** → find each Pi by hostname.
2. Open it → **Settings → Fixed IP Address** → set its IP from the table above → **Apply**.
3. Restart each Pi (`sudo reboot`, or power-cycle) so it picks up the fixed IP.
4. From the laptop (Ubuntu terminal), check all four answer:
   ```bash
   for ip in 240 241 242 243; do ping -c1 -W2 10.0.20.$ip >/dev/null && echo "10.0.20.$ip up" || echo "10.0.20.$ip DOWN"; done
   ```

## Step 3 - Prepare the laptop and connect to the Pis

In the laptop's **Ubuntu** terminal:

1. **If this is a new laptop**, install the tools once:
   ```bash
   sudo apt update && sudo apt install -y python3-pip git
   pip3 install --user "ansible-core==2.17.14"
   ~/.local/bin/ansible-galaxy collection install "community.general:==11.4.9" "ansible.posix:==2.2.2"
   ```
   Then copy the `cluster-ansible` folder back to `~/cluster-ansible`, and save the vault password:
   ```bash
   mkdir -p ~/.ansible && read -rsp 'Vault password: ' P && echo "$P" > ~/.ansible/cluster-vault-pass && unset P && chmod 600 ~/.ansible/cluster-vault-pass
   ```
2. **Forget the old Pis' fingerprints** (new OS = new SSH host keys) and accept the new ones:
   ```bash
   for ip in 240 241 242 243; do ssh-keygen -R 10.0.20.$ip; done
   for ip in 240 241 242 243; do ssh -o StrictHostKeyChecking=accept-new kube@10.0.20.$ip hostname; done
   ```
   Each should print its name (`master`, `node1`, ...).
3. **Load your SSH key** (do this in every new terminal before running playbooks):
   ```bash
   eval $(ssh-agent) && ssh-add
   cd ~/cluster-ansible
   ansible all -m ping
   ```
   All four should answer `pong`.

## Step 4 - USB disks (master and node1)

The playbooks mount each Longhorn disk by its label/ID:
master uses `LABEL=longhorn`, node1 uses the UUID in `inventory/host_vars/node1.yml`.

- **Same disks as before** (and you've checked the photo backup above): nothing to do.
- **New or wiped disks:** format each one with the `longhorn` label. **This erases the disk.**
  ```bash
  ssh kube@10.0.20.240 lsblk          # find the USB disk, usually /dev/sda
  ssh -t kube@10.0.20.240 'sudo wipefs -a /dev/sda && sudo mkfs.ext4 -L longhorn /dev/sda'
  ssh -t kube@10.0.20.241 'sudo wipefs -a /dev/sda && sudo mkfs.ext4 -L longhorn /dev/sda'
  ```
  Then in `inventory/host_vars/node1.yml` change `src: UUID=...` to `src: LABEL=longhorn`.

## Step 5 - Set up the Pis' operating system, then reboot

```bash
ansible-playbook playbooks/01-hosts.yml
ansible all -b -m reboot
```
This installs packages, kernel settings, firewall, fail2ban, the Pi firmware settings, disables
IPv6 and mounts the Longhorn disks. The reboot applies the firmware and kernel settings.
Wait 2 minutes, then check: `ansible all -m ping`.

## Step 6 - Build the cluster and install everything

```bash
ansible-playbook playbooks/site.yml
```
About 30-45 minutes. In order it:
1. Re-checks the OS setup (no changes this time).
2. Installs k3s: master first, then node2 and node3, then node1, using the join token from the vault.
3. Installs the platform: MetalLB (IPs 10.0.20.230-239), Traefik on 10.0.20.230, cert-manager,
   Longhorn, Prometheus + Grafana (with your dashboards), CloudNativePG.
4. Installs the apps: Cloudflare tunnel, portfolio site, onion site (same onion address, from the
   vault), Immich, Home Assistant + Z-Wave JS UI, Uptime Kuma, homepage, UniFi Poller.
5. Installs the daily Immich and cloudflared auto-update timers and the weekly etcd maintenance.

If it stops with an error, fix the cause and run the same command again; it picks up where things
differ.

**Check:**
```bash
ssh kube@10.0.20.240 sudo kubectl get nodes          # 4 nodes, all Ready
ssh kube@10.0.20.240 "sudo kubectl get pods -A | grep -vE 'Running|Completed'"   # ideally only the header line
```

## Step 7 - Put your app data back

```bash
ls backups/                       # pick the newest folder
ansible-playbook playbooks/restore-data.yml -e backup=backups/<newest-folder> -e confirm=yes
```
This restores the Immich database (users, albums, faces), the Home Assistant config, the Z-Wave
network, Uptime Kuma (monitors, history, login), and both websites' files.

**Photos - restore from S3.** The playbooks already recreated the `immich-s3-backup` secret from the
vault, so the restore job can read the bucket:
```bash
scp files/apps/immich/s3-restore-job.yaml kube@10.0.20.240:
ssh -t kube@10.0.20.240 '
  sudo kubectl -n immich scale deploy/immich-server --replicas=0 &&
  sudo kubectl apply -f s3-restore-job.yaml &&
  sudo kubectl -n immich wait --for=condition=complete job/immich-s3-restore --timeout=2h &&
  sudo kubectl -n immich logs job/immich-s3-restore --tail=3 &&
  sudo kubectl -n immich delete job immich-s3-restore &&
  sudo kubectl -n immich scale deploy/immich-server --replicas=1 && rm s3-restore-job.yaml'
```
Then open https://photos.hinkel-net.com: albums, people and pictures should all be back.

*If the laptop backups are gone too*, the S3 folder `immich-data/backups/` holds Immich's own daily
database dumps; restore the newest one following Immich's guide
(https://immich.app/docs/administration/backup-and-restore) or ask for help.

## Step 8 - Check everything works

| What | Where | Expected |
|---|---|---|
| Public sites | https://photos.hinkel-net.com, https://ha.hinkel-net.com, https://portfolio.hinkel-net.com | Load normally (Cloudflare tunnel reconnects by itself) |
| Homepage | https://www.hinkel-net.com | Cloudflare login, then Cognito + Authenticator code |
| Authentik | https://auth.lan.hinkel-net.com (home or VPN) | Your Authentik login + MFA (from the restored `authentik-db.dump`). Padlock shows a valid Let's Encrypt certificate (can take a few minutes after `03-platform.yml`) |
| Uptime Kuma | https://uptime.lan.hinkel-net.com | Authentik login, then your old Kuma login; all checks green within a few minutes |
| Grafana | https://grafana.lan.hinkel-net.com | "Sign in with Authentik", or login `admin` + password from the `prometheus-grafana` secret (it's **new** after a rebuild): `ssh kube@10.0.20.240 "sudo kubectl -n monitoring get secret prometheus-grafana -o jsonpath='{.data.admin-password}' \| base64 -d"` |
| Home Assistant | iPhone app | Works as before; Z-Wave devices respond |
| Onion site | Tor Browser | Same .onion address as before |
| Phone alerts | Uptime Kuma → Settings → Notifications → ntfy → Test | Alert arrives on your phone |

Then refresh the vault (it now holds the new Grafana password) and take a fresh backup:
```bash
ansible-playbook playbooks/create-vault.yml
ansible-playbook playbooks/backup.yml
```

---

## Things that live outside the cluster (nothing to rebuild)

- **Cloudflare**: DNS (including the DNS-only `*.lan.hinkel-net.com` record → 10.0.20.230), tunnel
  routes (all `http://10.0.20.230:80`), the www Access login, and the "Edit zone DNS" API token
  cert-manager uses (its value is in the vault).
- **AWS Cognito**: the www login accounts and Authenticator setup.
- **UniFi router**: its own settings and cloud backups (only the fixed IPs in step 2 change).
- **ntfy**: the phone alert topic.
- **GitHub (JLH1982/Cluster)**: a public copy of these scripts without secrets (no vault).

## Keep these safe (or a rebuild isn't possible)

- **The vault password** - saved in Edge. Consider a printed copy too.
- **Run `playbooks/backup.yml` regularly** (weekly is good). Each run also uploads this folder +
  that backup, encrypted with the vault password, to S3 bucket `<your-cluster-backup-bucket>`.
- **The AWS account** - the photo backup lives in S3 bucket `<your-immich-backup-bucket>`. Don't delete it or the
  `immich-s3-backup` IAM user.

## Laptop gone? Get this folder back from S3

Every `backup.yml` run uploads this whole folder (scripts, encrypted vault) plus that run's app-data
backup to S3, encrypted with the **Ansible vault password**.

1. Sign in to the **AWS console** (your own login) → **S3** → bucket **`<your-cluster-backup-bucket>`** → folder
   `cluster-ansible/` → select the newest `cluster-ansible-<date>.tar.gz.gpg` → **Download**.
   (The laptop's own AWS key can only upload, so downloading needs your console login.)
2. On the new laptop, set up Ubuntu/WSL and the tools (step 3.1 above), save the vault password to
   `~/.ansible/cluster-vault-pass`, then unpack the download (adjust the file name):
   ```bash
   sudo apt install -y gnupg
   cp /mnt/c/Users/<you>/Downloads/cluster-ansible-*.tar.gz.gpg ~/
   mkdir -p ~/cluster-ansible && gpg --batch --pinentry-mode loopback \
     --passphrase-file ~/.ansible/cluster-vault-pass -d ~/cluster-ansible-*.tar.gz.gpg | tar -xzf - -C ~/cluster-ansible
   ls ~/cluster-ansible/backups/        # the app-data backup from that run
   ```
3. Create a new SSH key (`ssh-keygen -t ed25519`), put its public key on the Pis in step 1, and
   continue with step 2.
4. To re-enable the S3 upload from the new laptop: in AWS IAM, user `cluster-ansible-backup` →
   create a new access key → `aws configure --profile cluster-backup` (region `us-east-2`).
   Deactivate the old key.
