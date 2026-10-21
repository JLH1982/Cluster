#!/usr/bin/env python3
# Runs on the master as root (via playbooks/create-vault.yml). Prints the cluster's secrets as
# YAML for the Ansible vault. Output is never logged or written to disk on the master.
import base64, json, subprocess, os, yaml

os.environ["KUBECONFIG"] = "/etc/rancher/k3s/k3s.yaml"

def secret(ns, name, raw=False):
    d = json.loads(subprocess.check_output(["kubectl", "-n", ns, "get", "secret", name, "-o", "json"]))["data"]
    return d if raw else {k: base64.b64decode(v).decode() for k, v in d.items()}

token = [l.split(":", 1)[1].strip() for l in open("/etc/rancher/k3s/config.yaml") if l.startswith("token:")][0]
db = secret("immich", "immich-postgres-secret")
out = {
    "vault_k3s_token": token,
    "vault_cloudflared_token": secret("cloudflare", "cloudflared-token")["token"],
    "vault_onion_keys_b64": secret("darkweb", "onion-keys", raw=True),
    "vault_immich_db_username": db["username"],
    "vault_immich_db_password": db["password"],
    "vault_longhorn_basic_auth": secret("longhorn-system", "longhorn-basic-auth"),
    "vault_zwave_basic_auth": secret("home-assistant", "zwave-basic-auth"),
    "vault_grafana_admin_password": secret("monitoring", "prometheus-grafana")["admin-password"],
    "vault_longhorn_ui_credentials": open("/home/kube/longhorn-ui-credentials.txt").read(),
    "vault_zwave_settings_json": open("/home/kube/zwave-settings.json").read(),
    "vault_uptime_kuma_metrics": secret("uptime-kuma", "uptime-kuma-metrics"),
    "vault_unpoller_unifi": secret("unpoller", "unpoller-unifi"),
}
print(yaml.safe_dump(out, sort_keys=False, width=1000))
