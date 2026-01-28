# Paperless AI Invoice Processor Setup Guide

This script integrates your Paperless-ngx instance (on TrueNAS Scale) with your PC's Ollama AI to automatically extract invoice data and organize files.

## Prerequisites

1.  **Ollama on PC:**
    *   Ensure Ollama is running on your PC.
    *   Ensure it accepts external connections. Set the environment variable `OLLAMA_HOST=0.0.0.0` on your PC.
    *   Pull the model: `ollama pull qwen2.5:32b` (or your preferred model).
2.  **Paperless-ngx on NAS:**
    *   Must be running and accessible.
    *   You need access to the `media/scripts` (or equivalent) folder mapped in your Docker container.

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

## Step 2: Configure the Script

You can configure the script either by editing the file directly OR by setting environment variables in your Docker container.

### Option A: Environment Variables (Recommended for Docker)
Add these variables to your Paperless container configuration in TrueNAS:
*   `PAPERLESS_API_TOKEN`: Your generated API Token.
*   `OLLAMA_HOST`: The URL of your PC (e.g., `http://192.168.1.100:11434`).
*   `OLLAMA_MODEL`: The model name (default: `qwen2.5:32b`).

### Option B: Edit the Script
1.  Open `paperless_ai_processor.py` in a text editor.
2.  **Update Variables:**
    *   `PAPERLESS_API_TOKEN`: Replace `YOUR_PAPERLESS_API_TOKEN_HERE`.
    *   `OLLAMA_HOST`: Replace `http://YOUR_PC_IP_HERE:11434`.

## Step 3: Installation on TrueNAS

1.  **Locate Scripts Folder:** Find the folder on your NAS that is mounted to `/usr/src/paperless/scripts` (or just `/scripts`) inside the Paperless container.
2.  **Copy Script:** Place `paperless_ai_processor.py` into this folder.
3.  **Permissions:** Ensure the script is executable. You might need to SSH into the NAS or Container and run:
    ```bash
    chmod +x /usr/src/paperless/scripts/paperless_ai_processor.py
    ```

## Step 4: Configure Post-Consumption Trigger

1.  To run immediately after a document is imported:
    *   There isn't a direct "GUI" setting for post-consumption scripts in some versions. You typically set the environment variable `PAPERLESS_POST_CONSUMPTION_SCRIPT` in your container configuration.
    *   Set it to: `/usr/src/paperless/scripts/paperless_ai_processor.py`
    *   **Restart** the Paperless container.

*Note: This will run the script for EVERY document. The script itself checks for the `AI processing needed` tag and exits immediately if it's missing, so it's safe.*

## Step 5: Setup "Batch Mode" (Retry)

If your PC is gaming (Ollama is busy), the script will skip processing. You need a way to retry later.

1.  **Cron Job:** Set up a cron job on the NAS (or inside the container if it persists) to run the script every hour.
    ```bash
    # Run every hour
    0 * * * * /usr/src/paperless/scripts/paperless_ai_processor.py
    ```
2.  When running without the `DOCUMENT_ID` environment variable, the script automatically enters **Batch Mode**, finds all documents with `AI processing needed`, and processes them.

## Usage Workflow

1.  Scan/Email an Invoice.
2.  **Important:** Ensure the import process adds the tag `AI processing needed`.
    *   You can do this via a **Workflow** in Paperless:
        *   Trigger: Document Added
        *   Filter: Mime Type is PDF (or specific mail rule)
        *   Action: Assign Tag `AI processing needed`
3.  Paperless consumes the file.
4.  The script runs.
    *   If Ollama is free -> Extracts data, moves file, updates fields, swaps tag to `AI processed`.
    *   If Ollama is busy -> Exits.
5.  If skipped, the hourly Cron job picks it up later.
