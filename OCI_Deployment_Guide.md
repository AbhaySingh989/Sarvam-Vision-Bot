# OCI Free Tier Deployment Guide (AMD)

This guide will help you deploy your **CoinSwitch Bot** on Oracle Cloud Infrastructure (OCI) Free Tier using **AMD** instances.

> [!WARNING]
> **Reclamation Policy**: OCI "Always Free" AMD instances will be **stopped** by Oracle if they are idle (CPU < 20%) for 7 days.
> **Solution**: Run multiple bots (requires Swap) OR use the "Keep-Alive" script.

---

## Prerequisites
1.  **OCI Account**: An active Oracle Cloud Free Tier account.
2.  **SSH Key Pair**: You will need an SSH public/private key pair. OCI can generate this for you during instance creation.

---

## Step 1: Create the AMD Instance

1.  Log in to the **OCI Console**.
2.  Navigate to **Compute** -> **Instances**.
3.  Click **Create Instance**.
4.  **Name**: Give it a name (e.g., `CoinSwitch-Bot-1`).
5.  **Image and Shape**:
    - Click **Edit**.
    - **Image**: Select **Canonical Ubuntu** (Version 22.04 or 24.04).
    - **Shape**: Select **VM.Standard.E2.1.Micro** (This is the Always Free AMD shape).
    - Click **Select Shape**.
6.  **Networking**:
    - If this is your first instance, select **Create new virtual cloud network**.
    - Keep defaults.
7.  **Add SSH Keys**:
    - Select **Generate a key pair for me**.
    - **IMPORTANT**: Click **Save Private Key** and save the `.key` file to your computer. You CANNOT recover this later.
8.  **Boot Volume**: Leave as default (50GB).
9.  Click **Create**.

*Wait a few minutes for the instance to turn Green (Running).*

---

## Step 2: Connect to Your Instance

1.  Copy the **Public IP Address** from the instance details page.
2.  Open your terminal (Command Prompt, PowerShell, or Putty).
3.  Move your downloaded private key to a safe folder (e.g., `C:\OCI\ssh-key-2024-11-23.key`).
4.  Connect using SSH:
    ```powershell
    # Replace path/to/key and public-ip
    ssh -i "path\to\your\private.key" ubuntu@<YOUR_PUBLIC_IP>
    ```
    *Note: If you get a "Permissions" error on Windows, you may need to restrict access to the key file or use Putty.*

### Troubleshooting: "Bad Permissions" / "Unprotected Private Key"
Windows sets file permissions too openly by default. SSH requires that **only you** can read the key file.

**Option 1: Fix via PowerShell (Fastest)**
Run this command in your terminal (replace the path with your actual key path):
```powershell
icacls "C:\OCI\ssh-key-2025-11-22.key" /inheritance:r /grant:r "$($env:USERNAME):(R)"
```
*Try connecting again after running this.*

**Option 2: Fix via GUI**
1.  Right-click your key file -> **Properties**.
2.  Go to **Security** tab -> Click **Advanced**.
3.  Click **Disable inheritance** -> **Convert inherited permissions into explicit permissions**.
4.  Remove **ALL** users from the list except **your own username**.
5.  Click **Apply** -> **OK**.

### Troubleshooting: No Public IP?
If you see a `-` instead of an IP address in the console:
1.  On the Instance Details page, scroll down to the **Resources** menu on the left.
2.  Click **Attached VNICs**.
3.  Click the **Name** of the VNIC (usually `primaryvnic`).
4.  Scroll down to **IPv4 Addresses**.
5.  Click the **three dots (...)** on the right side of the table row -> Select **Edit**.
6.  Under **Public IP Type**, select **Ephemeral Public IP**.
7.  Click **Update**.
    *   *Note: If this option is greyed out or fails, you likely created the instance in a "Private Subnet". You must **Terminate** this instance and create a new one. During creation, under **Networking**, make sure to select "Assign a public IPv4 address".*

---

## Step 3: System Setup & Swap Memory (CRITICAL for Multi-Bot)

The AMD instance only has **1GB RAM**. To run multiple bots (e.g., 5-10), you **MUST** enable Swap memory (using disk space as RAM).

1.  **Update System**:
    ```bash
    sudo apt update && sudo apt upgrade -y
    sudo apt install docker.io docker-compose-v2 -y
    sudo usermod -aG docker $USER
    newgrp docker
    ```

2.  **Enable 4GB Swap File**:
    ```bash
    # Create a 4GB file
    sudo fallocate -l 4G /swapfile
    
    # Secure the file
    sudo chmod 600 /swapfile
    
    # Mark it as swap space
    sudo mkswap /swapfile
    
    # Enable it
    sudo swapon /swapfile
    
    # Make it permanent
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
    ```

---

## Step 4: Deploy Multiple Bots

Running multiple bots increases CPU usage (helping avoid reclamation) but consumes more RAM.

1.  **Clone the Repository**:
    ```bash
    git clone https://github.com/AbhaySingh989/CoinSwitch_Bot_OCI_1_OCI_1.git
    ```
    *   **Private Repository?** If your repo is private, you cannot just clone it.
        1.  Go to GitHub -> Settings -> Developer Settings -> Personal Access Tokens -> Tokens (classic).
        2.  Generate a new token with `repo` scope.
        3.  Clone using the token:
            ```bash
            git clone https://<YOUR_TOKEN>@github.com/AbhaySingh989/CoinSwitch_Bot_OCI_1.git
            ```
    
    cd CoinSwitch_Bot_OCI_1
    ```

2.  **Configure Environment**:
    ```bash
    nano .env
    # Paste your API keys here
    ```

3.  **Use Docker Compose**:
    We have prepared a `docker-compose.yml` in the OCI folder to run multiple bots.
    
    ```bash
    # Copy the OCI files to the main directory
    cp OCI/docker-compose.yml .
    
    # Start 3 bots (Safe start)
    docker compose up -d
    ```

    *To add more bots, edit `docker-compose.yml` and copy the `bot_3` block to create `bot_4`, `bot_5`, etc.*

4.  **Check Usage**:
    Run `htop` (install with `sudo apt install htop`) to see CPU and RAM usage.
    - If CPU is > 20%, you are safe!
    - If CPU is < 20%, proceed to Step 5.

---

## Step 5: Managing Your Bots (Advanced)

### 1. Accessing Individual Data
Each bot stores its data in a separate folder inside `CoinSwitch_Bot_OCI_1/bot_data/`.
- **Bot 1 Data**: `~/CoinSwitch_Bot_OCI_1/bot_data/bot_1/Crypto_Data.db`
- **Bot 2 Data**: `~/CoinSwitch_Bot_OCI_1/bot_data/bot_2/Crypto_Data.db`

You can download these files using an SFTP client (like FileZilla) using the same SSH key.

### 2. Updating a Single Bot
If you modify the code and want to update *only* Bot 2:
```bash
# Rebuild and restart ONLY bot_2
docker compose up -d --build bot_2
```
*Other bots will continue running without interruption.*

### 3. Using Multiple Git Repositories
If you want to test different strategies from different repos:
1.  Create a `strategies` folder: `mkdir strategies`
2.  Clone your repos there:
    ```bash
    git clone https://github.com/user/strategy_a.git strategies/strategy_a
    git clone https://github.com/user/strategy_b.git strategies/strategy_b
    ```
3.  Edit `docker-compose.yml` and change the `build` context:
    ```yaml
    bot_1:
      build:
        context: ./strategies/strategy_a
    ```

### 4. API Rate Limits (CRITICAL)
Running 10 bots means 10x the API requests.
- **Monitor**: Check your CoinSwitch dashboard for rate limit warnings.
- **Adjust**: If you get errors, increase the `INTERVAL` in `config.py` or add a delay in the code.

### 5. Sharing Access with a Friend
You asked about **Private vs. Public Keys**:
- **Private Key (`.key` or `.pem`)**: This is like your **House Key**. **NEVER SHARE THIS.** If you give this to your friend, they have full control forever, and you can't revoke it easily without changing the lock.
- **Public Key (`.pub`)**: This is like the **Lock**. You can put this lock on your server door. Your friend brings their own Private Key (House Key) that fits *their* Public Key (Lock).

**To give your friend access safely:**
1.  Ask your friend to generate their own SSH Key pair on their computer.
2.  Ask them to send you **ONLY their Public Key** (it looks like `ssh-rsa AAAAB3Nza...`).
3.  Log in to your server.
4.  Run this command to open the authorized keys file:
    ```bash
    nano ~/.ssh/authorized_keys
    ```
5.  Paste your friend's Public Key on a new line at the bottom.
6.  Save and Exit (`Ctrl+X`, `Y`, `Enter`).

Now your friend can log in using *their* Private Key, and you use *yours*. If you want to remove them later, just delete their line from that file!

---

## Step 6: The "Keep-Alive" Mechanism (Fallback)

If you are running only 1-2 bots and CPU is low (< 20%), use this script to prevent reclamation.

1.  **Create the Script**:
    ```bash
    mkdir -p ~/OCI
    nano ~/OCI/keep_alive.py
    # (Paste content from local keep_alive.py)
    ```

2.  **Create & Start Service**:
    ```bash
    sudo nano /etc/systemd/system/keep_alive.service
    # (Paste content from local keep_alive.service)
    
    sudo systemctl daemon-reload
    sudo systemctl enable --now keep_alive
    ```
