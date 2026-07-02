# TVPM Validation Tool: Feature Reference & Project Analysis Report

This document provides a comprehensive analysis of the **TVPM Validation Tool** codebase. It outlines the system architecture, details the local SQLite caching and Ollama AI integration, and walks through the feature workflows.

---

## 🏗️ System Architecture

The project is structured as a decoupled web application with a **FastAPI backend** (serving API requests) and a **Streamlit frontend** (interactive UI). It communicates with the internal LGE JIRA instance to validate issue applicability status based on SoC details.

To optimize performance and add intelligent reasoning capabilities, the system uses a **local SQLite cache** to avoid redundant JIRA API calls, and interfaces with a **local Ollama LLM** to analyze compatibility tables and support natural language queries.

```mermaid
sequenceDiagram
    actor User
    participant FE as Frontend (Streamlit)
    participant BE as Backend (FastAPI)
    participant DB as SQLite Cache (tvpm_cache.db)
    participant J as LGE JIRA API (or Mock response.json)
    participant O as Local Ollama LLM (AI)

    %% Validation Hub Flow
    Note over User, O: -- Validation Hub Flow --
    User->>FE: Upload Excel/HTML & enter Target SoC Name
    FE->>BE: POST /api/validate (column, target_soc, mock, use_ai)
    rect rgb(240, 240, 240)
        Note over BE: For each TVPM ID in batch (asyncio.Semaphore 15)
        BE->>DB: Query cached JIRA data
        alt Cache Miss or Force Refresh
            BE->>J: Fetch JIRA Issue (asynchronously)
            J-->>BE: JIRA JSON payload
            BE->>DB: Upsert Issue payload (raw_json, status, summary)
        else Cache Hit
            DB-->>BE: Cached JSON payload
        end
        Note over BE: Auto-detect SoC field in payload
        alt use_ai = True
            BE->>O: Query compatibility prompt (SoC Details, target_soc)
            O-->>BE: Returns JSON (status, reason)
        else use_ai = False
            Note over BE: Standard regex/table parser resolves status
        end
    end
    BE->>BE: Compile new DataFrame & write Excel
    BE-->>FE: Return Excel stream
    FE->>User: Show success dashboard & download button

    %% TVPM Chat Assistant Flow
    Note over User, O: -- TVPM Chat Assistant Flow --
    User->>FE: Select TVPM key & ask natural language question
    FE->>BE: POST /api/chat-tvpm (key, question)
    BE->>DB: Fetch issue details & raw JIRA JSON
    DB-->>BE: JIRA JSON
    BE->>O: Send context-rich chat prompt (Issue Details + Question)
    O-->>BE: Return answer text
    BE-->>FE: Return answer JSON
    FE->>User: Render chatbot response
```

---

## 📋 Detailed Feature Walkthrough

### 1. Excel & HTML Ingestion

- **Robust Ingestion (`load_excel_or_html`)**: LGE JIRA systems often export search results as HTML tables but save them with `.xls` or `.xlsx` extensions. The backend parses the file header. If it detects `<!doctype html`, `<html`, or `<table`, it decodes the content as UTF-8 and uses `pd.read_html`; otherwise, it defaults to standard `pd.read_excel`.

### 2. Dynamic Field Auto-Detection (Heuristics)

- **Heuristic Resolver (`auto_detect_soc_field`)**: Instead of forcing the user to manually configure JIRA custom fields, the backend automatically scans the issue payload for:
  - Any text field containing a JIRA table (delimited by `||` or `|`) with headers containing `"SOC"` or `"OS"`.
  - Any string field with exact values like `"O"`, `"X"`, or `"THE"`.
- If found, it automatically uses that field path (e.g. `fields.customfield_36507`) to extract compatibility details.

### 3. Local SQLite Caching

- **Cache Database (`tvpm_cache.db`)**: Stores fetched JIRA issue data locally in a `tvpm_issues` table containing:
  - `key` (TEXT Primary Key)
  - `summary` (TEXT)
  - `soc_details` (TEXT)
  - `status` (TEXT)
  - `raw_json` (TEXT - complete JIRA JSON response)
- This allows rapid local evaluation, offline mock mode operations, and feeds context into the AI Chat Assistant without making repetitive network calls.

### 4. Local AI Mapping & Reasoning (Ollama)

- **AI Compatibility Evaluation**: If "Use Local AI" is checked, the backend prompts the local Ollama LLM to parse the SoC compatibility details.
- Ollama evaluates whether the issue is applicable for the target SoC model and returns structured JSON containing:
  - `status`: Either `"Applicable"` or `"Not Applicable"`
  - `reason`: A brief, human-readable justification of the mapping decision (e.g., *"Model k24 is marked O in the compatibility table, meaning it is supported."*)
- This reasoning is appended as an extra **AI Reason** column in the Excel output.

### 5. Interactive TVPM Chat Assistant

- **Natural Language Assistant**: The user can select any cached TVPM key from a dropdown and ask natural language questions.
- The `/api/chat-tvpm` endpoint builds a context-rich prompt containing the issue's key, summary, description, and custom fields, and feeds it into the local Ollama model to generate context-specific, technically accurate answers.

### 6. Formatted Excel Export

- Generates a fresh `.xlsx` spreadsheet with standard columns:
  - `S.No`: Row serial number
  - `TVPM ID`: JIRA Issue Key
  - `SoC Details`: Raw value or extracted status from the table
  - `Status`: Evaluated status (`Applicable` / `Not Applicable` / `Error: <details>`)
  - `AI Reason` (Optional): The explanation returned by Ollama
