#!/usr/bin/env python3
import os
import sys
import json
import requests
import datetime
import logging
from typing import Optional, Dict, Any

# ======================================================================================
# CONFIGURATION
# ======================================================================================

# PAPERLESS CONFIGURATION
# -----------------------
# URL of your Paperless instance (usually localhost:8000 if running on same stack,
# or the container name/IP).
PAPERLESS_URL = os.environ.get("PAPERLESS_URL", "http://localhost:8000")

# API Token: Generate this in Paperless Admin > API Tokens
PAPERLESS_API_TOKEN = os.environ.get("PAPERLESS_API_TOKEN", "YOUR_PAPERLESS_API_TOKEN_HERE")

# OLLAMA CONFIGURATION
# --------------------
# IP address of your PC running Ollama (e.g., http://192.168.1.50:11434)
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://YOUR_PC_IP_HERE:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:32b")  # Adjust based on your available models
OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", 5))  # Seconds to wait for a "ping" check before assuming busy/offline

# LOGIC CONFIGURATION
# -------------------
REQUIRED_TAG = "AI processing needed"
PROCESSED_TAG = "AI processed"
STORAGE_PATH_NAME = "Invoices"

# CUSTOM FIELDS MAPPING
# Map the internal keys used by the AI to the EXACT names of Custom Fields in Paperless
CUSTOM_FIELD_MAP = {
    "invoice_number": "Invoice Number",
    "total_amount": "Total Amount",
    "net_amount": "Net Amount",
    "vat_amount": "VAT Amount",
    "payment_due_date": "Payment Due Date",
    "seller_vat_id": "Seller UID/VAT ID"
}

# ======================================================================================
# SETUP LOGGING
# ======================================================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        # You can add a FileHandler here if you want logs on disk
    ]
)
logger = logging.getLogger(__name__)

# ======================================================================================
# CLIENTS
# ======================================================================================

class PaperlessClient:
    def __init__(self, base_url, token):
        self.base_url = base_url.rstrip('/')
        self.headers = {
            "Authorization": f"Token {token}",
            "Content-Type": "application/json"
        }

    def get_document(self, doc_id: int) -> Dict[str, Any]:
        resp = requests.get(f"{self.base_url}/api/documents/{doc_id}/", headers=self.headers)
        resp.raise_for_status()
        return resp.json()

    def get_document_content(self, doc_id: int) -> str:
        """Fetches the OCRd content of the document."""
        # Note: The main document endpoint usually contains the 'content' field.
        doc = self.get_document(doc_id)
        return doc.get("content", "")

    def update_document(self, doc_id: int, data: Dict[str, Any]):
        resp = requests.patch(f"{self.base_url}/api/documents/{doc_id}/", headers=self.headers, json=data)
        resp.raise_for_status()
        return resp.json()

    def get_tag_id(self, name: str) -> Optional[int]:
        params = {"name__iexact": name}
        resp = requests.get(f"{self.base_url}/api/tags/", headers=self.headers, params=params)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if results:
            return results[0]["id"]
        return None

    def create_tag(self, name: str) -> int:
        resp = requests.post(f"{self.base_url}/api/tags/", headers=self.headers, json={"name": name, "color": "#c42345"})
        resp.raise_for_status()
        return resp.json()["id"]

    def get_correspondent_id(self, name: str) -> Optional[int]:
        params = {"name__iexact": name}
        resp = requests.get(f"{self.base_url}/api/correspondents/", headers=self.headers, params=params)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if results:
            return results[0]["id"]
        return None

    def create_correspondent(self, name: str) -> int:
        resp = requests.post(f"{self.base_url}/api/correspondents/", headers=self.headers, json={"name": name, "match": "", "matching_algorithm": 6}) # 6 = Auto
        resp.raise_for_status()
        return resp.json()["id"]

    def get_storage_path_id(self, name: str) -> Optional[int]:
        params = {"name__iexact": name}
        resp = requests.get(f"{self.base_url}/api/storage_paths/", headers=self.headers, params=params)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if results:
            return results[0]["id"]
        return None

    def get_custom_fields(self) -> Dict[str, int]:
        """Returns a dict of {Field Name: Field ID}"""
        fields = {}
        next_url = f"{self.base_url}/api/custom_fields/"
        while next_url:
            resp = requests.get(next_url, headers=self.headers)
            resp.raise_for_status()
            data = resp.json()
            for item in data.get("results", []):
                fields[item["name"]] = item["id"]
            next_url = data.get("next")
        return fields

    def search_documents_by_tag(self, tag_id: int) -> list:
        """Finds all documents with a specific tag."""
        docs = []
        next_url = f"{self.base_url}/api/documents/?tags__id__all={tag_id}"
        while next_url:
            resp = requests.get(next_url, headers=self.headers)
            resp.raise_for_status()
            data = resp.json()
            docs.extend(data.get("results", []))
            next_url = data.get("next")
        return docs


class OllamaClient:
    def __init__(self, host, model, timeout=5):
        self.host = host.rstrip('/')
        self.model = model
        self.timeout = timeout

    def is_available(self) -> bool:
        """Checks if Ollama is reachable and not overloaded."""
        try:
            # Simple version check or model list to ping
            resp = requests.get(f"{self.host}/api/tags", timeout=self.timeout)
            if resp.status_code == 200:
                return True
        except requests.RequestException:
            pass
        return False

    def extract_invoice_data(self, ocr_text: str) -> Dict[str, Any]:
        """
        Sends the OCR text to Ollama and asks for JSON extraction.
        """
        # Truncate text if too long to fit context window (simple safety)
        # 32k tokens is a lot, but let's be safe with ~50k chars
        safe_text = ocr_text[:50000]

        prompt = f"""
        You are an intelligent data extraction assistant.
        Analyze the following invoice text and extract the specific metadata in strictly valid JSON format.

        Required Fields:
        - vendor_name: (String) Name of the company/seller.
        - invoice_date: (String) Format YYYY-MM-DD.
        - payment_due_date: (String) Format YYYY-MM-DD.
        - invoice_number: (String) The invoice identifier.
        - total_amount: (Number/Float) The final total amount (Brutto).
        - net_amount: (Number/Float) The net amount before tax.
        - vat_amount: (Number/Float) The tax amount.
        - seller_vat_id: (String) The VAT ID / UID of the seller.
        - currency: (String) e.g., EUR, USD.

        Instructions:
        - If a field is not found, use null.
        - Convert all dates to YYYY-MM-DD.
        - Do not include markdown code blocks (like ```json), just the raw JSON string.
        - Be precise.

        INVOICE TEXT:
        {safe_text}
        """

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json" # Force JSON mode if supported by model version
        }

        try:
            resp = requests.post(f"{self.host}/api/generate", json=payload, timeout=120) # Longer timeout for generation
            resp.raise_for_status()
            result = resp.json()
            response_text = result.get("response", "")

            # Clean up potential markdown code blocks if the model adds them despite instructions
            response_text = response_text.replace("```json", "").replace("```", "").strip()

            return json.loads(response_text)
        except Exception as e:
            logger.error(f"Ollama generation failed: {e}")
            return {}

# ======================================================================================
# MAIN LOGIC
# ======================================================================================

def process_document(doc_id: int, paperless: PaperlessClient, ollama: OllamaClient):
    logger.info(f"Processing Document ID: {doc_id}")

    # 1. Fetch Document
    try:
        doc = paperless.get_document(doc_id)
    except Exception as e:
        logger.error(f"Could not fetch document {doc_id}: {e}")
        return

    current_tags = doc.get("tags", [])

    # 2. Check Tag "AI processing needed" (Tag ID)
    # We resolve the tag ID first
    needed_tag_id = paperless.get_tag_id(REQUIRED_TAG)
    if not needed_tag_id:
        logger.warning(f"Tag '{REQUIRED_TAG}' not found in Paperless. Nothing to do.")
        return

    if needed_tag_id not in current_tags:
        logger.info(f"Document {doc_id} does not have the '{REQUIRED_TAG}' tag. Skipping.")
        return

    # 3. Check Ollama Availability
    if not ollama.is_available():
        logger.warning("Ollama instance is busy or unreachable. Skipping processing for now.")
        return

    # 4. Get Content
    content = doc.get("content", "")
    if not content:
        logger.warning(f"Document {doc_id} has no content (OCR might still be running). Skipping.")
        return

    # 5. Extract Data
    logger.info(f"Sending document {doc_id} content to Ollama...")
    data = ollama.extract_invoice_data(content)

    if not data:
        logger.error("Failed to extract data or empty response from AI.")
        return

    logger.info(f"Extracted Data: {json.dumps(data, indent=2)}")

    # 6. Prepare Updates
    updates = {}
    custom_fields_updates = []

    # A. Vendor / Correspondent
    vendor_name = data.get("vendor_name")
    if vendor_name:
        c_id = paperless.get_correspondent_id(vendor_name)
        if not c_id:
            logger.info(f"Creating new correspondent: {vendor_name}")
            c_id = paperless.create_correspondent(vendor_name)
        updates["correspondent"] = c_id

    # B. Created Date (Invoice Date)
    inv_date = data.get("invoice_date")
    if inv_date:
        # Validate date format roughly
        try:
            datetime.datetime.strptime(inv_date, "%Y-%m-%d")
            updates["created"] = inv_date
        except ValueError:
            logger.warning(f"Invalid date format received: {inv_date}")

    # C. Storage Path
    sp_id = paperless.get_storage_path_id(STORAGE_PATH_NAME)
    if sp_id:
        updates["storage_path"] = sp_id
    else:
        logger.warning(f"Storage path '{STORAGE_PATH_NAME}' not found.")

    # D. Custom Fields
    # First, get all existing field IDs to map names to IDs
    existing_fields_map = paperless.get_custom_fields()

    # We need to construct the 'custom_fields' list for the PATCH request.
    # Paperless API expects: [{"field": <id>, "value": <value>}, ...]
    # BUT! Replacing the list might wipe others.
    # Safest is to read current custom fields, update/append, and send back.
    current_custom_fields = doc.get("custom_fields", [])
    # Convert current to a dict for easy updating: {field_id: value}
    cf_data_map = {item["field"]: item["value"] for item in current_custom_fields}

    for ai_key, paperless_name in CUSTOM_FIELD_MAP.items():
        if paperless_name in existing_fields_map:
            field_id = existing_fields_map[paperless_name]
            val = data.get(ai_key)
            if val is not None:
                # Handle Monetary types (sometimes require special formatting, but usually string/float works)
                # Paperless often expects Monetary to be just the number if currency is implicit, or we might need to check field type.
                # Assuming simple value assignment works for now.
                cf_data_map[field_id] = val
        else:
            logger.warning(f"Custom field '{paperless_name}' not defined in Paperless.")

    # Reconstruct the list
    updates["custom_fields"] = [{"field": fid, "value": val} for fid, val in cf_data_map.items()]

    # 7. Apply Updates
    if updates:
        logger.info(f"Applying updates to document {doc_id}...")
        try:
            paperless.update_document(doc_id, updates)
        except Exception as e:
            logger.error(f"Failed to update document: {e}")
            return

    # 8. Update Tags (Swap tags)
    processed_tag_id = paperless.get_tag_id(PROCESSED_TAG)
    if not processed_tag_id:
        processed_tag_id = paperless.create_tag(PROCESSED_TAG)

    new_tags = [t for t in current_tags if t != needed_tag_id]
    if processed_tag_id not in new_tags:
        new_tags.append(processed_tag_id)

    try:
        paperless.update_document(doc_id, {"tags": new_tags})
        logger.info(f"Successfully processed Document {doc_id}.")
    except Exception as e:
        logger.error(f"Failed to update tags: {e}")


def main():
    # Environment Check
    doc_id_env = os.environ.get("DOCUMENT_ID")

    # Initialize Clients
    paperless = PaperlessClient(PAPERLESS_URL, PAPERLESS_API_TOKEN)
    ollama = OllamaClient(OLLAMA_HOST, OLLAMA_MODEL, OLLAMA_TIMEOUT)

    if doc_id_env:
        # SINGLE MODE (Post-consumption)
        logger.info(f"Starting in Single Mode for Document ID {doc_id_env}")
        process_document(int(doc_id_env), paperless, ollama)
    else:
        # BATCH MODE
        logger.info("Starting in Batch Mode")
        needed_tag_id = paperless.get_tag_id(REQUIRED_TAG)
        if not needed_tag_id:
            logger.error(f"Tag '{REQUIRED_TAG}' does not exist. Create it in Paperless first.")
            return

        docs_to_process = paperless.search_documents_by_tag(needed_tag_id)
        logger.info(f"Found {len(docs_to_process)} documents to process.")

        for doc in docs_to_process:
            process_document(doc["id"], paperless, ollama)

if __name__ == "__main__":
    main()
