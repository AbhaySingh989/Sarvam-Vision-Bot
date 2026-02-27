# OCI CI/CD Setup (Existing VM)

This project deploys to your existing OCI instance using GitHub Actions + Docker + SSH (no OCIR required).

## 1) One-time setup on OCI VM

SSH into your existing instance and run:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y docker.io docker-compose-v2
sudo usermod -aG docker $USER
newgrp docker
```

Create deployment folder:

```bash
sudo mkdir -p /opt/sarvam-telegram-bot
sudo chown -R $USER:$USER /opt/sarvam-telegram-bot
```

Create runtime env file:

```bash
cat > /opt/sarvam-telegram-bot/.env <<'EOF'
TELEGRAM_BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN
SARVAM_API_KEY=YOUR_SARVAM_API_KEY
SARVAM_BASE_URL=https://api.sarvam.ai
SARVAM_VISION_LANGUAGE=en-IN
SARVAM_VISION_OUTPUT_FORMAT=md
SARVAM_CHAT_MODEL=sarvam-m
SARVAM_POLL_INTERVAL_SECONDS=2.5
SARVAM_POLL_TIMEOUT_SECONDS=480
CHAT_CONTEXT_CHAR_LIMIT=6000
SESSION_TTL_MINUTES=60
LOG_LEVEL=INFO
EOF
chmod 600 /opt/sarvam-telegram-bot/.env
```

## 2) GitHub repository Variables

Add these in GitHub -> Settings -> Secrets and variables -> Actions -> Variables:

- `DEPLOY_PATH` (example: `/opt/sarvam-telegram-bot`)
- `SSH_PORT` (example: `22`)

## 3) GitHub repository Secrets

Add these in GitHub -> Settings -> Secrets and variables -> Actions -> Secrets:

- `OCI_VM_HOST` (public IP or DNS)
- `OCI_VM_USER` (example: `ubuntu` or `opc`)
- `OCI_VM_SSH_PRIVATE_KEY` (full PEM/private key contents)

## 4) Workflow behavior

- `CI`: validates Python + Docker build on PRs and non-main pushes.
- `CD - Deploy to OCI VM (SSH Deploy)`:
  - runs on push to `main` (or manual dispatch)
  - builds Docker image in GitHub Actions
  - uploads image tarball + deploy files to VM over SSH
  - loads image on VM and restarts bot container using `docker compose`

## 5) First deploy

1. Push this repository to GitHub.
2. Configure all Variables and Secrets above.
3. Push to `main` branch.
4. Watch actions:
   - CI
   - CD - Deploy to OCI VM (SSH Deploy)

## 6) Manual redeploy to an older commit/tag

Run workflow: `CD - Deploy to OCI VM (SSH Deploy)` using **Run workflow** and set:

- `git_ref=<branch|tag|commit>`
