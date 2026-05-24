# Deploy

Ansible playbook to update and redeploy the stack on `boil-ubu`.

## Requirements

- Ansible installed locally
- Collections:
  ```sh
  ansible-galaxy collection install ansible.posix community.docker
  ```
- SSH access to `boil-ubu` configured in `~/.ssh/config` (no password)
- Docker + Docker Compose v2 installed on the remote host

## Usage

From this directory:

```sh
ansible-playbook deploy.yml
```

Override the remote path if needed:

```sh
ansible-playbook deploy.yml -e remote_path=/srv/iwouldliketopay
```

## What it does

1. Rsyncs the project to the remote host, respecting `.gitignore` (so `.env`,
   `config/xray-config.json`, and `data/**` content stay on the host).
2. Pulls the latest images.
3. Recreates and starts the compose services.

## First-time bootstrap

The first deploy will not include `.env` or `config/xray-config.json` (gitignored).
SSH to the host once and run:

```sh
cd /opt/iwouldliketopay
./scripts/bootstrap.sh
# then edit config/xray-config.json with real values
```
