# OCI CI/CD Setup (Existing VM)

This project deploys to your existing OCI instance using GitHub Actions + Docker + OCIR.

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

- `OCIR_REGISTRY` (example: `iad.ocir.io`)
- `OCIR_NAMESPACE` (your tenancy namespace)
- `IMAGE_NAME` (example: `sarvam-telegram-bot`)
- `DEPLOY_PATH` (example: `/opt/sarvam-telegram-bot`)
- `SSH_PORT` (example: `22`)

## 3) GitHub repository Secrets

Add these in GitHub -> Settings -> Secrets and variables -> Actions -> Secrets:

- `OCIR_USERNAME` (format: `<namespace>/<oracle_username>`)
- `OCIR_AUTH_TOKEN` (Oracle auth token, not account password)
- `OCI_VM_HOST` (public IP or DNS)
- `OCI_VM_USER` (example: `ubuntu` or `opc`)
- `OCI_VM_SSH_PRIVATE_KEY` (full PEM/private key contents)

## 4) Workflow behavior

- `CI`: validates Python + Docker build on PRs and non-main pushes.
- `CD - Build and Push Image to OCIR`: builds image and pushes tags:
  - `latest`
  - `sha-<7-char-commit>`
- `CD - Deploy to OCI VM`:
  - auto-runs after successful image build/push
  - logs into OCIR on VM
  - pulls and restarts container via `docker compose`
- `Rollback - OCI VM`:
  - manual rollback to a previous image tag.

## 5) First deploy

1. Push this repository to GitHub.
2. Configure all Variables and Secrets above.
3. Push to `main` branch.
4. Watch actions:
   - Build/Push workflow
   - Deploy workflow

## 6) Manual rollback

Run workflow: `Rollback - OCI VM` and pass:

- `image_tag=sha-abcdef1`

or

- `image_tag=latest`
