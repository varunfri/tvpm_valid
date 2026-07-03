import os
import re
import asyncio
import sqlite3
import json
import pandas as pd
import httpx
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import StreamingResponse
from typing import Optional, List, Dict, Any
from io import BytesIO
from dotenv import load_dotenv

# Load default environment variables
load_dotenv()

app = FastAPI(title="TVPM Validation API", version="1.0.0")

# Database Configuration
DB_PATH = "tvpm_cache.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tvpm_issues (
            key TEXT PRIMARY KEY,
            summary TEXT,
            soc_details TEXT,
            status TEXT,
            raw_json TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

# Run initialization
init_db()

def upsert_cached_issue(key: str, summary: str, soc_details: str, status: str, raw_json_dict: dict):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        raw_json_str = json.dumps(raw_json_dict)
        cursor.execute("""
            INSERT INTO tvpm_issues (key, summary, soc_details, status, raw_json, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                summary=excluded.summary,
                soc_details=excluded.soc_details,
                status=excluded.status,
                raw_json=excluded.raw_json,
                updated_at=CURRENT_TIMESTAMP
        """, (key, summary, soc_details, status, raw_json_str))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error caching issue {key} to SQLite: {e}")

# Helper to load Excel file or HTML table disguised as Excel
def load_excel_or_html(file_contents: bytes) -> pd.DataFrame:
    """
    Checks if the uploaded file is HTML (often exported as .xls/.xlsx by LGE systems) 
    and reads it using read_html with proper UTF-8 decoding to prevent encoding/corrupt issues.
    Otherwise, falls back to read_excel.
    """
    preview = file_contents[:150].strip().lower()
    if b"<!doctype html" in preview or b"<html" in preview or b"<table" in preview:
        import io
        html_str = file_contents.decode("utf-8", errors="replace")
        dfs = pd.read_html(io.StringIO(html_str))
        if dfs:
            return dfs[0]
    return pd.read_excel(BytesIO(file_contents))

# Helper to traverse nested fields in JIRA response
def get_nested_value(data: Dict[str, Any], path: str) -> Any:
    """
    Safely get a value from a dictionary using a dot-separated path (e.g., 'fields.summary' or 'fields.customfield_10100').
    If 'fields.' prefix is missing and field exists under fields, we automatically add it.
    """
    parts = path.split('.')
    # Auto-resolve missing 'fields.' prefix for custom/standard fields
    if parts[0] not in ('fields', 'id', 'key', 'self', 'expand') and 'fields' in data:
        if parts[0] in data['fields']:
            parts.insert(0, 'fields')
            
    current = data
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
            
    # Post-process common JIRA object formats
    if isinstance(current, dict):
        if "value" in current:
            return current["value"]
        if "name" in current:
            return current["name"]
        if "key" in current:
            return current["key"]
    return current

def extract_status_from_jira_table(table_text: str, soc_model: str) -> Optional[str]:
    """
    Parses a JIRA table string and looks for the row where the SOC column matches soc_model.
    Returns the value of the status column (usually the last column).
    """
    if not table_text or not isinstance(table_text, str) or not soc_model:
        return None
        
    lines = table_text.strip().split('\n')
    soc_col_idx = -1
    status_col_idx = -1
    target_soc = soc_model.strip().lower()
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        # Parse header
        if line.startswith('||') and line.endswith('||'):
            # Split headers
            headers = [h.strip().lower() for h in line.split('||')[1:-1]]
            for idx, h in enumerate(headers):
                if 'soc' in h:
                    soc_col_idx = idx
                elif h == '' or h == ' ' or 'status' in h or 'result' in h:
                    status_col_idx = idx
            
            if status_col_idx == -1 and headers:
                status_col_idx = len(headers) - 1
            continue
            
        # Parse data rows
        if line.startswith('|') and line.endswith('|') and not line.startswith('||'):
            cols = [c.strip() for c in line.split('|')[1:-1]]
            
            temp_soc_idx = soc_col_idx if soc_col_idx != -1 else 1
            temp_status_idx = status_col_idx if status_col_idx != -1 else (len(cols) - 1)
            
            if temp_soc_idx < len(cols) and temp_status_idx < len(cols):
                row_soc = cols[temp_soc_idx].strip().lower()
                if row_soc == target_soc or target_soc in row_soc:
                    return cols[temp_status_idx].strip()
                    
    return None

# Helper to clean and resolve status based on SoC details value
def map_soc_status(val: Any) -> str:
    if val is None:
        return "Not Applicable"
    
    val_str = str(val).strip().lower()
    
    # if ('X') => Not Applicable
    # else if ('O' || 'THE' || 'the' ) => Applicable
    if val_str == 'x':
        return "Not Applicable"
    elif val_str in ('o', 'the'):
        return "Applicable"
    else:
        # Fallback default rule
        return "Not Applicable"

def check_mock_availability(mock: bool) -> bool:
    if not mock:
        return False
    filepath = "response.json"
    if not os.path.exists(filepath):
        filepath = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "response.json")
    if not os.path.exists(filepath):
        print("[WARNING] mock=True requested but response.json not found. Disabling mock mode.")
        return False
    return True


async def fetch_jira_issue(client: httpx.AsyncClient, base_url: str, pat: str, key: str, sem: asyncio.Semaphore, mock: bool = False) -> Dict[str, Any]:
    """
    Fetches a single issue from JIRA API using the /rest/api/2/issue/{key} endpoint.
    If mock=True, loads and returns mock data from response.json.
    Uses a Semaphore to throttle concurrent requests.
    """
    print(f"[DEBUG] fetch_jira_issue: key={key}, mock={mock} (type={type(mock)}), base_url={base_url}")
    mock = check_mock_availability(mock)
    if mock:
        try:
            filepath = "response.json"
            if not os.path.exists(filepath):
                filepath = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "response.json")
            
            with open(filepath, "r", encoding="utf-8") as f:
                issue_data = json.load(f)
            # Update key to requested key
            issue_data["key"] = key
            return {"key": key, "status": "success", "data": issue_data}
        except Exception as e:
            return {"key": key, "status": "error", "error": f"Mock error loading response.json: {str(e)}"}


    clean_base = base_url.rstrip('/')
    if not clean_base.endswith("/issue") and "/issue/" not in clean_base:
        url = f"{clean_base}/issue/rest/api/2/issue/{key}"
    else:
        url = f"{clean_base}/rest/api/2/issue/{key}"
    print(f"[DEBUG] fetch_jira_issue: Dispatching live request to URL: {url}")
    headers = {
        "Authorization": f"Bearer {pat}",
        "Content-Type": "application/json"
    }
    
    async with sem:
        try:
            response = await client.get(url, headers=headers, timeout=10.0)
            if response.status_code == 200:
                return {"key": key, "status": "success", "data": response.json()}
            elif response.status_code == 404:
                return {"key": key, "status": "not_found", "error": "Issue not found"}
            elif response.status_code == 401:
                return {"key": key, "status": "unauthorized", "error": "Unauthorized / Invalid PAT"}
            else:
                return {"key": key, "status": "error", "error": f"HTTP {response.status_code}"}
        except httpx.RequestError as e:
            return {"key": key, "status": "error", "error": f"Connection error: {str(e)}"}


async def call_ollama(ollama_url: str, model: str, prompt: str, json_mode: bool = False) -> Dict[str, Any]:
    url = f"{ollama_url.rstrip('/')}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False
    }
    if json_mode:
        payload["format"] = "json"
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=45.0)
            if response.status_code == 200:
                res = response.json()
                text = res.get("response", "").strip()
                if json_mode:
                    try:
                        return json.loads(text)
                    except Exception as e:
                        # Attempt to parse json from text if it has markdown wrapper or other artifacts
                        clean_text = re.sub(r"^```json\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
                        return json.loads(clean_text)
                return {"text": text}
            else:
                return {"error": f"Ollama HTTP {response.status_code}"}
    except Exception as e:
        return {"error": f"Ollama Connection Error: {str(e)}"}

def make_field_suggestion_prompt(fields_preview: dict) -> str:
    fields_info = []
    for path, info in fields_preview.items():
        val = info["value"]
        val_str = str(val)[:200] if val is not None else "None"
        fields_info.append(f"Field: {path} (Type: {info['type']})\nSample Value:\n{val_str}\n---")
    
    fields_block = "\n".join(fields_info)
    
    prompt = f"""You are an expert LGE JIRA assistant.
Analyze the following list of JIRA fields and their sample values to identify which field contains the SoC compatibility details (often containing values like 'O', 'X', 'THE', or a table/text describing SoC models with compatibility letters).

Available Fields:
{fields_block}

Respond ONLY in JSON format with keys:
"suggested_field": the exact field path (e.g. "fields.customfield_36507")
"reasoning": a brief explanation of why this field represents the SoC details.
"""
    return prompt

def make_compatibility_prompt(field_value: str, soc_model: str, summary: str = "") -> str:
    prompt = f"""You are an expert QA compatibility verification AI.
Analyze the following SoC details value/table and decide if it is applicable to the target SoC model: '{soc_model}'.
Context - Issue Summary: {summary}

SoC Details Field Value:
{field_value}

Rules:
- If the status for the target model is 'X', it is 'Not Applicable'.
- If the status is 'O', 'THE', or 'the', it is 'Applicable'.
- If the target model is NOT mentioned or is marked otherwise, default to 'Not Applicable'.
- Explain the reason for your decision in a single concise sentence.

Respond ONLY in JSON format with keys:
"status": "Applicable" or "Not Applicable"
"reason": a clear, brief explanation of your decision (e.g. "Model {soc_model} is marked with 'O' in the webOS26 row of the compatibility table").
"""
    return prompt

def make_chat_prompt(issue_data: dict, question: str) -> str:
    summary = issue_data.get("fields", {}).get("summary", "No Summary")
    description = issue_data.get("fields", {}).get("description", "No Description")
    
    # Grab any text fields
    text_fields = []
    for key, val in issue_data.get("fields", {}).items():
        if isinstance(val, str) and len(val.strip()) > 0 and key not in ("summary", "description"):
            text_fields.append(f"{key}: {val[:300]}")
    text_fields_str = "\n".join(text_fields)
    
    prompt = f"""You are a helpful AI assistant for the LGE TVPM JIRA tool.
Answer questions about the following TVPM JIRA issue details.

Key: {issue_data.get("key", "Unknown")}
Summary: {summary}
Description: {description}
Other Fields:
{text_fields_str}

User Question: {question}

Provide a concise, helpful, and technically accurate answer based on the JIRA issue data provided.
"""
    return prompt


def auto_detect_soc_field(issue_data: Dict[str, Any]) -> Optional[str]:
    fields = issue_data.get("fields", {})
    # 1. Search for fields containing a compatibility table
    for key, val in fields.items():
        if isinstance(val, str) and "||" in val:
            val_lower = val.lower()
            if "||os||soc||" in val_lower or "||soc||" in val_lower:
                return f"fields.{key}"
            lines = val.strip().split("\n")
            for line in lines:
                if line.startswith("||") and line.endswith("||"):
                    headers = [h.strip().lower() for h in line.split("||")[1:-1]]
                    if any("soc" in h for h in headers):
                        return f"fields.{key}"
                        
    # 2. Fallback to fields containing exact compatibility letters like O, X, THE (less specific)
    for key, val in fields.items():
        if isinstance(val, str):
            val_strip = val.strip().lower()
            if val_strip in ("o", "x", "the"):
                return f"fields.{key}"
                
    return None


@app.get("/api/health")
def health():
    return {"status": "ok"}



@app.post("/api/fields-preview")
async def fields_preview(
    file: UploadFile = File(...),
    column_name: Optional[str] = Form(None),
    jira_base_url: Optional[str] = Form(None),
    jira_pat: Optional[str] = Form(None),
    mock: bool = Form(False),
    use_ai: bool = Form(False),
    ollama_url: Optional[str] = Form(None),
    ollama_model: Optional[str] = Form(None)
):
    """
    Upload an Excel file, extract the first TVPM ID, query JIRA, and return
    a preview of all available fields and their values in JIRA.
    """
    # Load configuration
    base_url = jira_base_url or os.getenv("JIRA_BASE_URL")
    pat = jira_pat or os.getenv("JIRA_PAT")
    
    mock = check_mock_availability(mock)
    
    if not mock and (not base_url or not pat):
        raise HTTPException(status_code=400, detail="JIRA Base URL and PAT are required (either in .env or passed as form parameters)")
        
    try:
        contents = await file.read()
        df = load_excel_or_html(contents)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse Excel file: {str(e)}")

    if df.empty:
        raise HTTPException(status_code=400, detail="The uploaded Excel file is empty.")

    # Find the TVPM column
    target_col = column_name
    if not target_col:
        # Try content-based auto-detection first (columns containing JIRA/TVPM-like keys)
        jira_pattern = re.compile(r'^[A-Z0-9]+-\d+$')
        detected_col = None
        for col in df.columns:
            # Check the first 5 non-null rows of this column
            sample = df[col].dropna().head(5).astype(str).tolist()
            if sample and all(jira_pattern.match(val.strip()) for val in sample):
                detected_col = col
                break
        
        if detected_col:
            target_col = detected_col
        else:
            # Fallback to column name matching (but avoid false positives like 'Side')
            detected = []
            for c in df.columns:
                c_lower = str(c).lower()
                if 'tvpm' in c_lower or 'issue key' in c_lower:
                    detected.append(c)
                elif re.search(r'\bid\b|_id|id_', c_lower) and 'side' not in c_lower:
                    detected.append(c)
            
            if detected:
                target_col = detected[0]
            else:
                target_col = df.columns[0]  # Fallback to first column

    if target_col not in df.columns:
        raise HTTPException(status_code=400, detail=f"Column '{target_col}' not found in Excel file.")

    # Extract the first non-null TVPM ID
    tvpm_ids = df[target_col].dropna().astype(str).tolist()
    valid_ids = [tid.strip() for tid in tvpm_ids if tid.strip()]
    
    if not valid_ids:
        raise HTTPException(status_code=400, detail=f"No TVPM IDs found in column '{target_col}'")

    first_id = valid_ids[0]
    
    # Query JIRA for the first issue to get its fields (passing mock value)
    sem = asyncio.Semaphore(1)
    async with httpx.AsyncClient() as client:
        res = await fetch_jira_issue(client, base_url, pat, first_id, sem, mock=mock)
        
    if res["status"] != "success":
        raise HTTPException(status_code=400, detail=f"Failed to fetch issue {first_id} from JIRA: {res.get('error')}")

    # Extract and flatten fields list
    issue_data = res["data"]
    fields_dict = issue_data.get("fields", {})
    
    # Create a nice dictionary of field name/path and current value for the user to choose
    preview_fields = {}
    for key, val in fields_dict.items():
        # Get path as 'fields.fieldname'
        path = f"fields.{key}"
        # Extract value nicely
        resolved_val = get_nested_value(issue_data, path)
        preview_fields[path] = {
            "raw_key": key,
            "value": resolved_val,
            "type": type(val).__name__
        }

    # Cache in SQLite database
    summary = fields_dict.get("summary", "No Summary")
    upsert_cached_issue(
        key=first_id,
        summary=summary,
        soc_details="",
        status="Previewed",
        raw_json_dict=issue_data
    )

    # Optional Local AI field suggestion using Ollama
    ai_suggestion = None
    if use_ai and ollama_url and ollama_model:
        prompt = make_field_suggestion_prompt(preview_fields)
        ai_res = await call_ollama(ollama_url, ollama_model, prompt, json_mode=True)
        if "error" not in ai_res:
            ai_suggestion = {
                "suggested_field": ai_res.get("suggested_field"),
                "reasoning": ai_res.get("reasoning")
            }
        else:
            ai_suggestion = {
                "suggested_field": None,
                "reasoning": f"Failed to contact local Ollama: {ai_res['error']}"
            }
        
    # Auto-detect SoC field
    detected_soc_field = auto_detect_soc_field(issue_data)
        
    return {
        "tvpm_id_used": first_id,
        "fields": preview_fields,
        "issue_key": issue_data.get("key"),
        "ai_suggestion": ai_suggestion,
        "detected_soc_field": detected_soc_field
    }



@app.post("/api/validate")
async def validate_tvpm(
    file: UploadFile = File(...),
    column_name: str = Form(...),
    soc_field: Optional[str] = Form(None),
    soc_model: Optional[str] = Form(None),
    jira_base_url: Optional[str] = Form(None),
    jira_pat: Optional[str] = Form(None),
    mock: bool = Form(False),
    use_ai: bool = Form(False),
    ollama_url: Optional[str] = Form(None),
    ollama_model: Optional[str] = Form(None)
):
    """
    Process the Excel sheet:
    - Extracts TVPM IDs from the specified column.
    - Queries JIRA in parallel (live or mocked).
    - Resolves the SoC details.
    - Maps to Status (Applicable / Not Applicable) using rules or local AI.
    - Caches results to SQLite.
    - Generates a new Excel sheet and returns it.
    """
    base_url = jira_base_url or os.getenv("JIRA_BASE_URL")
    pat = jira_pat or os.getenv("JIRA_PAT")
    
    mock = check_mock_availability(mock)
    
    masked_pat = pat[:4] + "..." if pat else "None"
    print(f"[DEBUG] validate_tvpm called: mock={mock} (type={type(mock)}), base_url={base_url}, pat={masked_pat}")
    
    if not mock and (not base_url or not pat):
        raise HTTPException(status_code=400, detail="JIRA Base URL and PAT are required.")

    try:
        contents = await file.read()
        df = load_excel_or_html(contents)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse Excel file: {str(e)}")

    if column_name not in df.columns:
        raise HTTPException(status_code=400, detail=f"Column '{column_name}' not found in Excel file.")

    # Extract all TVPM IDs
    tvpm_ids = df[column_name].dropna().astype(str).tolist()
    unique_ids = []
    # Preserve order but keep unique IDs
    seen = set()
    for tid in tvpm_ids:
        cleaned = tid.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            unique_ids.append(cleaned)

    if not unique_ids:
        raise HTTPException(status_code=400, detail=f"No TVPM IDs found in column '{column_name}'.")

    # Limit concurrency to 15 parallel requests
    sem = asyncio.Semaphore(15)
    
    async with httpx.AsyncClient() as client:
        tasks = [fetch_jira_issue(client, base_url, pat, key, sem, mock=mock) for key in unique_ids]
        responses = await asyncio.gather(*tasks)

    # Process responses and create the output data
    output_rows = []
    for s_no, (key, res) in enumerate(zip(unique_ids, responses), start=1):
        soc_val = None
        status = "Not Applicable"
        err_msg = None
        soc_val_display = ""
        ai_reason = ""
        
        if res["status"] == "success":
            issue_data = res["data"]
            # Extract SoC details using the specified or auto-detected field path
            actual_soc_field = soc_field
            if not actual_soc_field or actual_soc_field.strip() == "":
                actual_soc_field = auto_detect_soc_field(issue_data)
                
            if actual_soc_field:
                soc_val = get_nested_value(issue_data, actual_soc_field)
            else:
                soc_val = None

            
            # Determine mapping: local AI (Ollama) vs standard rules
            if use_ai and ollama_url and ollama_model and soc_val is not None:
                summary = issue_data.get("fields", {}).get("summary", "")
                prompt = make_compatibility_prompt(str(soc_val), soc_model or "", summary)
                ai_res = await call_ollama(ollama_url, ollama_model, prompt, json_mode=True)
                
                if "error" not in ai_res:
                    status = ai_res.get("status", "Not Applicable")
                    ai_reason = ai_res.get("reason", "")
                    soc_val_display = str(soc_val)
                else:
                    status = f"Error: {ai_res['error']}"
                    ai_reason = f"Ollama execution failed: {ai_res['error']}"
                    soc_val_display = str(soc_val)
            else:
                # Standard parsing logic
                if soc_model and isinstance(soc_val, str) and '|' in soc_val:
                    extracted_status = extract_status_from_jira_table(soc_val, soc_model)
                    if extracted_status is not None:
                        status = map_soc_status(extracted_status)
                        soc_val_display = f"{extracted_status} (Extracted for '{soc_model}')"
                    else:
                        status = "Not Applicable"
                        soc_val_display = f"Not Found (SOC Model '{soc_model}' not in table)"
                else:
                    status = map_soc_status(soc_val)
                    soc_val_display = str(soc_val) if soc_val is not None else ""
            
            # Upsert into local SQLite Cache
            upsert_cached_issue(
                key=key,
                summary=issue_data.get("fields", {}).get("summary", "No Summary"),
                soc_details=soc_val_display,
                status=status,
                raw_json_dict=issue_data
            )
        else:
            err_msg = res.get("error", "Unknown error")
            status = f"Error: {err_msg}"
            soc_val_display = f"Error ({err_msg})"
            ai_reason = f"Failed to retrieve JIRA issue details: {err_msg}"
            
        row = {
            "S.No": s_no,
            "TVPM ID": key,
            "SoC Details": soc_val_display,
            "Status": status
        }
        if use_ai:
            row["AI Reason"] = ai_reason
            
        output_rows.append(row)

    # Create new DataFrame
    output_df = pd.DataFrame(output_rows)
    
    # Write to Excel in-memory
    output_buffer = BytesIO()
    with pd.ExcelWriter(output_buffer, engine='openpyxl') as writer:
        output_df.to_excel(writer, index=False, sheet_name="Validation Results")
        
    output_buffer.seek(0)
    
    # Return Excel file as streaming response
    return StreamingResponse(
        output_buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=TVPM_Validation_Results.xlsx"}
    )


@app.get("/api/cached-tvpms")
def get_cached_tvpms():
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT key, summary, status FROM tvpm_issues ORDER BY updated_at DESC")
        rows = cursor.fetchall()
        conn.close()
        return [{"key": r[0], "summary": r[1], "status": r[2]} for r in rows]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@app.post("/api/chat-tvpm")
async def chat_tvpm(
    payload: Dict[str, Any]
):
    key = payload.get("key")
    question = payload.get("question")
    ollama_url = payload.get("ollama_url", "http://localhost:11434")
    ollama_model = payload.get("ollama_model", "llama3")
    
    if not key or not question:
        raise HTTPException(status_code=400, detail="TVPM Key and Question are required.")
        
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT raw_json FROM tvpm_issues WHERE key = ?", (key,))
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            raise HTTPException(status_code=404, detail=f"TVPM Issue '{key}' not found in local cache. Run validation first.")
            
        issue_data = json.loads(row[0])
        prompt = make_chat_prompt(issue_data, question)
        ai_res = await call_ollama(ollama_url, ollama_model, prompt, json_mode=False)
        
        if "error" in ai_res:
            raise HTTPException(status_code=500, detail=ai_res["error"])
            
        return {"response": ai_res.get("text", "")}
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


