# Paperless AI Invoice Processor Setup Guide

This script integrates your Paperless-ngx instance (on TrueNAS Scale) with your PC's Ollama AI to automatically extract invoice data and organize files.

## Prerequisites

1.  **Ollama on PC:**
    *   Ensure Ollama is running on your PC.
    *   Ensure it accepts external connections. Set the environment variable `OLLAMA_HOST=0.0.0.0` on your PC.
    *   Pull the model: `ollama pull qwen2.5:32b` (or your preferred model).
2.  **Paperless-ngx on TrueNAS Scale:**
    *   Must be running.
    *   You need to generate an API Token in Paperless (Settings -> Admin -> API Tokens).

## Step 1: Prepare Paperless-ngx

Before running the script, you must create the following items in your Paperless Web UI:

1.  **Tags:**
    *   `AI processing needed` (The script triggers on this).
    *   `AI processed` (The script applies this when done).
2.  **Storage Path:**
    *   Name: `Invoices`
    *   Path Template: `Invoices/{{ created_year }}/{{ correspondent }}/{{ title }}` (or your preference).
3.  **Custom Fields:**
    *   Create these EXACT fields (Type is important):
        *   `Invoice Number` (Text)
        *   `Total Amount` (Monetary)
        *   `Net Amount` (Monetary)
        *   `VAT Amount` (Monetary)
        *   `Payment Due Date` (Date)
        *   `Seller UID/VAT ID` (Text)

## Step 2: Installation on TrueNAS Scale

The best way to run this script on TrueNAS Scale is to execute it *inside* the Paperless container using a Cron Job defined in the TrueNAS UI. This ensures all Python dependencies (like `requests`) are available without modifying the host system.

### 2.1 Place the Script
1.  Access your TrueNAS datasets (via SMB or Shell).
2.  Place `paperless_ai_processor.py` in a folder that is **mounted** inside your Paperless container.
    *   *Default TrueNAS App:* The `/library` dataset is often mounted to `/usr/src/paperless/data` or `/usr/src/paperless/media`.
    *   *Recommendation:* Put it in your Paperless `media` folder, e.g., `.../paperless/media/scripts/paperless_ai_processor.py`.

### 2.2 Configure Environment Variables
You need to pass the configuration to the script. Since we will run this via Cron, we can either hardcode them in the script (Option A) or pass them in the cron command (Option B).

**Option A (Edit Script - Easiest):**
Open `paperless_ai_processor.py` and edit the top section:
```python
PAPERLESS_URL = "http://localhost:8000" # Localhost works because script runs INSIDE container
PAPERLESS_API_TOKEN = "your_actual_token_here"
OLLAMA_HOST = "http://192.168.1.55:11434" # IP of your PC
```

**Option B (Environment Variables):**
You will add these to the Cron command later.

## Step 3: Setup Automation (Cron Job)

To ensure documents are processed even if your PC was off, we set up a Cron Job to run the script periodically (e.g., every hour).

1.  **Log in to TrueNAS Web UI.**
2.  Go to **System Settings** -> **Advanced** -> **Cron Jobs**.
3.  Click **Add**.
4.  **Description:** `Paperless AI Processing`
5.  **Command:**
    We need a command that finds the running Paperless pod and executes the script inside it. Use this command:

    ```bash
    k3s kubectl get pods -n ix-paperless-ngx -o name | grep paperless-ngx | head -n 1 | xargs -I {} k3s kubectl exec -n ix-paperless-ngx {} -- python3 /usr/src/paperless/media/scripts/paperless_ai_processor.py
    ```

    *Important Notes on the Command:*
    *   `-n ix-paperless-ngx`: This is the default namespace for the TrueNAS Official App. If you named your app differently (e.g. `paperless`), change this to `-n ix-paperless`. You can check namespaces via `k3s kubectl get ns`.
    *   `/usr/src/paperless/media/scripts/...`: This path depends on where you put the file in **Step 2.1**. Adjust the path to match where the file lives *inside* the container.
    *   If you chose **Option B** for variables, the command looks like:
        ```bash
        k3s kubectl get pods -n ix-paperless-ngx -o name | grep paperless-ngx | head -n 1 | xargs -I {} k3s kubectl exec -n ix-paperless-ngx --env PAPERLESS_API_TOKEN=xxx --env OLLAMA_HOST=http://192.168.1.55:11434 {} -- python3 /usr/src/paperless/media/scripts/paperless_ai_processor.py
        ```

6.  **Schedule:**
    *   Select `Hourly` or `Daily` (e.g., run at 2 AM and 2 PM).
7.  **User:** `root` (Required to run kubectl).
8.  **Save.**

### 2.3 Immediate Trigger (Optional)
If you want the script to run *immediately* after a document is consumed (real-time), you can set the `PAPERLESS_POST_CONSUMPTION_SCRIPT` environment variable in the TrueNAS App Config:

1.  Edit the Paperless App in TrueNAS.
2.  Add Environment Variable:
    *   Key: `PAPERLESS_POST_CONSUMPTION_SCRIPT`
    *   Value: `/usr/src/paperless/media/scripts/paperless_ai_processor.py`
3.  **Note:** This works alongside the Cron Job. The script is smart enough to exit if Ollama is offline, and the Cron Job will pick it up later.

## Usage Workflow

1.  **Scan/Import:** Upload a document to Paperless.
2.  **Tag:** Ensure it gets the tag `AI processing needed`.
    *   *Tip:* Create a Workflow in Paperless: "If document created, assign tag 'AI processing needed'".
3.  **Processing:**
    *   The script (via Cron or Post-Consumption) checks availability of your PC.
    *   If PC is **On**: Data is extracted, fields updated, tag swapped to `AI processed`.
    *   If PC is **Off**: Script exits. The next Cron run will retry.
