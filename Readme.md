# TVPM Validation Project

This project provides a web-based utility tool to validate TVPM (TV Project Management) issue data by querying the LGE JIRA REST API, with built-in local SQLite caching and offline AI Chat capabilities.

---

## 📋 Features

* **Excel/HTML Upload**: Drag and drop any Excel file or HTML table disguised as `.xls`/`.xlsx` exported from LGE systems.
* **Column Auto-Detection**: Automatically identifies the column containing TVPM IDs/JIRA keys by checking cell contents against JIRA key patterns.
* **JIRA Mock Mode (Offline)**: Check the **Mock Mode** box in the sidebar to simulate JIRA API responses offline using the cached `response.json` file.
* **Dynamic JIRA Field Auto-Detection (Heuristics)**: Automatically scans each issue's payload to locate the SoC compatibility table/field behind the scenes. No manual field mapping required.
* **High Performance API**: Backend utilizes asynchronous execution to run multiple JIRA requests concurrently.
* **Applicability Mapping**: Automatically processes SoC Details and applies the status rules:
  * `'X'` $\rightarrow$ **Not Applicable**
  * `'O'` / `'THE'` / `'the'` $\rightarrow$ **Applicable**
  * Others $\rightarrow$ **Not Applicable**
* **Local SQLite Caching**: Caches fetched JIRA issue data locally in a `tvpm_cache.db` database.
* **Local Ollama AI Integration**: Connects to a local Ollama server (e.g. Llama3) to:
  * Run **AI-powered compatibility analysis** with human-readable explanation reasoning.
  * Power an interactive **TVPM Chat Assistant** tab where you can select a cached TVPM and ask natural language questions.
* **Excel Export**: Generates and formats a fresh result sheet with `S.No`, `TVPM ID`, `SoC Details`, and `Status` (and optional `AI Reason` if AI is enabled) columns.

---

## 📂 Project Structure

```text
tvpm_valid/
├── .env                  # Local environment configuration file (ignored from git)
├── .env.example          # Template environment config file
├── requirements.txt      # Project library dependencies (includes lxml for HTML-disguised XLS)
├── run.py                # Service runner script supporting Windows/macOS/Linux port management
├── README.md             # Documentation (this file)
├── response.json         # Local JIRA issue template used for Mock Mode
├── tvpm_cache.db         # Local SQLite database caching fetched issues (gitignored)
├── backend/
│   ├── main.py           # FastAPI backend server containing logic & routes
│   └── test_api.py       # Mock JIRA API unit tests for verification
└── frontend/
    └── app.py            # Streamlit web UI components & interactive tabs layout
```

---

## 🚀 Quick Start Guide

### 1. Prerequisites

* **Python 3.8+** installed.
* **Ollama** installed locally (Optional, only needed for AI Chat / AI Analysis features. Start it using `ollama run llama3`).

### 2. Set Up Virtual Environment & Dependencies

Create and activate a python virtual environment, then install requirements:

**On macOS / Linux:**

```bash
# Create venv
python3 -m venv .venv

# Activate venv
source .venv/bin/activate

# Install requirements
pip install -r requirements.txt
```

**On PowerShell:**

```powershell
# Create venv
python -m venv .venv

# Activate venv
.\.venv\Scripts\Activate.ps1

# Install requirements
pip install -r requirements.txt
```

### 3. Environment Setup

Create a `.env` file in the project root directory (you can copy `.env.example` as a template):

```env
# JIRA API Settings
JIRA_BASE_URL=https://jira.lge.com
JIRA_PAT=your_personal_access_token_here

# Port Configurations
BACKEND_PORT=8050
FRONTEND_PORT=8501

# Ollama Local AI Settings
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=llama3
```

> [NOTE]
> You can also leave `JIRA_PAT` blank if you only plan to run the tool in **Mock Mode** using the local `response.json` mock file.

### 4. Run the Project

Make sure your virtual environment is active, then start both backend and frontend concurrently:

```bash
python run.py
```

This will launch:

* **FastAPI Backend**: `http://127.0.0.1:8050`
* **Streamlit Web UI**: `http://127.0.0.1:8501`

The browser will open automatically at the Streamlit URL.

---

## 🧪 Verification & Testing

To run unit tests verifying JIRA connection, parsing, and status mapping rules:

```bash
.venv/bin/python backend/test_api.py
```

Outputs:

```text
Ran 3 tests in 0.156s
OK
```
