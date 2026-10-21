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
| `01-hosts.yml` | OS setup on all Pis: packages, kernel settings, IPv6 off, firewall, fail2ban, Longhorn disks |
| `02-k3s.yml` | Installs k3s (master first, then node2/node3, then node1). Never touches a running node |
| `03-platform.yml` | MetalLB, Traefik, cert-manager, Longhorn, Prometheus/Grafana, CloudNativePG |
| `04-apps.yml` | Cloudflare tunnel, portfolio site, onion site, Immich, Home Assistant + Z-Wave, Uptime Kuma, homepage (www), UniFi Poller |
| `05-maintenance.yml` | Weekly etcd defrag, daily Immich patch-update and daily cloudflared update timers on the master |
| `backup.yml` | Copies app data to `backups/<date-time>/` on this laptop |
| `restore-data.yml` | Puts a backup's data back into a (re)built cluster |
| `create-vault.yml` | Reads the cluster's secrets into the encrypted vault |

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
- `website.tgz` and `darkweb-site.tgz`: the two websites' files
- `etcd-snapshot.db`: every Kubernetes object, secrets included

**Not included: the Immich photo files themselves** (up to 250 GB). Those need an off-site backup,
such as a Longhorn backup target on Backblaze B2. Until one is set up, the only copies of your photos
are the two Longhorn replicas, on the master's and node1's USB disks.

The backup folder contains secrets, so keep it private, and don't commit it to git.

## Disaster recovery

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
4. Restore the Immich photos from the off-site backup.
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
- The **Immich photo files** (see above).
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
