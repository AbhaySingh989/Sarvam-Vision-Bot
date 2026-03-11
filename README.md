# 🤖 Sarvam Vision + Chat Telegram Bot

## 👤 Author

**Abhay Singh**
- 📧 Email: `abhay.rkvv@gmail.com`
- 🐙 GitHub: [AbhaySingh989](https://github.com/AbhaySingh989)
- 💼 LinkedIn: [Abhay Singh](https://www.linkedin.com/in/abhay-pratap-singh-905510149/)

---

## 📖 About

This project is a Telegram bot prototype that combines:

- 👁️ **Sarvam Vision** for OCR/document extraction
- 💬 **Sarvam Chat** for summarization and question answering

It is designed to quickly demo what your team can do with PDF/image document intelligence in one chat workflow.

## ✨ Features

### 🚀 Core Features

- 📄 **Complete OCR**: Extract full text from PDF/image
- 🧠 **TL;DR**: Quick summary of the uploaded document
- 🔑 **Key Points**: Important bullet points from extracted text
- ✅ **Action Items**: Structured tasks from content
- ❓ **Ask Question**: Ask grounded questions on the latest document

### 🎨 UX Features

- 🔘 Inline action buttons after each upload
- ☰ Telegram command menu (`/ocr`, `/tldr`, `/keypoints`, `/actions`, `/ask`)
- ⏳ Emoji-based progress updates during Vision processing
- 🧭 “What next?” action prompt after each response

### 🛡️ Reliability & Safety

- 🔁 Vision retries on transient backend failures (e.g. circuit breaker/open)
- 📏 Chat prompt-size backoff on token limit errors
- 🧹 OCR cleanup removes noisy metadata blobs (like embedded base64 image dumps)
- 🔒 Sensitive logging redaction + transport log suppression

## 🛠️ Installation

### Prerequisites

- 🐍 Python 3.10+ recommended
- 📱 Telegram account
- 🔑 Sarvam API key
- 🤖 Telegram bot token from `@BotFather`

### Step 1: Clone

```bash
git clone <your-repo-url>
cd "Sarvam Bot Telegram"
```

### Step 2: Create Virtual Environment

```bash
python -m venv .venv
.venv\Scripts\activate
```

### Step 3: Install Dependencies

```bash
pip install -r requirements.txt
```

## ⚙️ Setup

Copy `.env.example` to `.env` (or edit existing `.env`) and set values:

```env
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
```

> ⚠️ Never commit real tokens/keys. `.env` is ignored by `.gitignore`.

## 🚀 Usage

### Start the Bot

```bash
python main.py
```

Direct package entrypoint:

```bash
python -m bot.main
```

### In Telegram

1. Send `/start`
2. Upload PDF/JPG/JPEG/PNG
3. Pick action from buttons or command menu:
   - `/ocr`
   - `/tldr`
   - `/keypoints`
   - `/actions`
   - `/ask`
4. For `/ask`, either:
   - use `/ask <your question>`, or
   - tap **Ask Question** button and send question in next message

## 📊 Processing Flow

Vision pipeline:

1. Create job
2. Get upload URL
3. Upload file
4. Start job
5. Poll status
6. Download output
7. Clean OCR text artifacts

Chat pipeline:

1. Build grounded prompt from extracted text
2. Handle prompt-too-long by reducing context
3. Return formatted Telegram output

## 📁 Project Structure

```text
Sarvam Bot Telegram/
├── .github/workflows/
│   ├── ci.yml
│   └── cd-deploy-ssh.yml
├── deploy/
│   ├── remote-deploy.sh
│   └── OCI_CICD_SETUP.md
├── Dockerfile
├── docker-compose.prod.yml
├── main.py
├── bot/
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

## 🚢 OCI Deployment (Existing VM)

CI/CD is configured for:

1. Build Docker image
2. Upload image + deploy files to your existing OCI VM via SSH
3. Load image and run container on the VM

See setup guide:

- `deploy/OCI_CICD_SETUP.md`

Required GitHub **Variables**:

- `DEPLOY_PATH`
- `SSH_PORT`

Required GitHub **Secrets**:

- `OCI_VM_HOST`
- `OCI_VM_USER`
- `OCI_VM_SSH_PRIVATE_KEY`

## 🐛 Troubleshooting

### Bot starts but OCR hangs at Pending

- Usually provider-side queue/backpressure.
- Bot now retries transient failures automatically.
- Wait for retry cycle to complete.

### OCR output contains metadata/noise

- Fixed via OCR cleanup logic.
- If still seen, share sample output to tune additional filters.

### Ask/TL;DR fails with prompt too long

- Bot now auto-reduces context and retries.
- You can also reduce `CHAT_CONTEXT_CHAR_LIMIT` in `.env`.

### Token/key exposure concern

- Rotate leaked tokens immediately.
- Keep only `.env.example` in git; never commit real `.env`.

## 🤝 Contributing

1. Fork
2. Create branch
3. Commit changes
4. Open PR

---

**Built for fast team demos of Sarvam document intelligence in Telegram.**

## V2 Architecture

The monolithic `bot.py` has been refactored into a modular architecture:
- `bot/config.py`: Configuration model
- `bot/state.py`: Multi-step state definitions
- `bot/clients/`: API client abstractions for Sarvam Vision and Chat
- `bot/engines/`: Document layout parsers and logic
- `bot/contracts/`: Strict Pydantic models for structured output
- `bot/workflows/`: State transition and processing flows (Extraction, Comparison, Entity)
- `bot/export/`: XLSX generation logic via openpyxl
- `bot/router.py`: Handles Telegram input routing
- `bot/main.py`: Entrypoint and wire-up

## Run Instructions

```bash
# Install new dependencies (openpyxl, pydantic)
pip install -r requirements.txt

# Run main bot
python main.py

# Run tests
PYTHONPATH=. pytest tests/
```
