import streamlit as st
import re
import pandas as pd
import httpx
import os
from io import BytesIO
from dotenv import load_dotenv

# Load default environment variables
load_dotenv()

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

# Page config
st.set_page_config(
    page_title="TVPM Validation Tool",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom premium CSS
st.markdown("""
<style>
    .main-title {
        font-size: 3rem;
        font-weight: 800;
        background: linear-gradient(90deg, #FF4B4B, #FF8F8F);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0.5rem;
    }
    .subtitle {
        font-size: 1.2rem;
        color: #6C7A89;
        margin-bottom: 2rem;
    }
    .status-applicable {
        background-color: #D4EDDA;
        color: #155724;
        padding: 0.3rem 0.6rem;
        border-radius: 4px;
        font-weight: 600;
        text-align: center;
    }
    .status-not-applicable {
        background-color: #F8D7DA;
        color: #721C24;
        padding: 0.3rem 0.6rem;
        border-radius: 4px;
        font-weight: 600;
        text-align: center;
    }
    .card {
        background-color: #f9fbfd;
        border: 1px solid #e3e8ee;
        border-radius: 8px;
        padding: 1.5rem;
        margin-bottom: 1rem;
    }
</style>
""", unsafe_allow_html=True)

# Sidebar Configuration
st.sidebar.image("https://img.icons8.com/color/96/jira.png", width=64)
st.sidebar.title("JIRA API Settings")
st.sidebar.markdown("Configure LGE JIRA API Connection")

# Get defaults from environment
default_url = os.getenv("JIRA_BASE_URL", "https://jira.lge.com")
default_pat = os.getenv("JIRA_PAT", "")
backend_port = os.getenv("BACKEND_PORT", "8050")
default_backend_url = f"http://127.0.0.1:{backend_port}"

backend_url = st.sidebar.text_input("FastAPI Backend URL", value=default_backend_url)
mock_mode = st.sidebar.checkbox("Mock Mode (Offline response.json)", value=True, help="Enable this to simulate JIRA requests offline using cached response.json data.")

jira_url = None
jira_pat = None
if not mock_mode:
    jira_url = st.sidebar.text_input("JIRA Base URL", value=default_url)
    jira_pat = st.sidebar.text_input("JIRA PAT (Personal Access Token)", value=default_pat, type="password")

st.sidebar.markdown("---")
st.sidebar.title("🤖 Ollama AI Settings")
use_ai = st.sidebar.checkbox("Enable Local AI Analysis", value=False)
ollama_url = st.sidebar.text_input("Ollama URL", value=os.getenv("OLLAMA_URL", "http://localhost:11434"))
ollama_model = st.sidebar.text_input("Ollama Model", value=os.getenv("OLLAMA_MODEL", "llama3"))

st.sidebar.markdown("---")
st.sidebar.info(
    "💡 **Instructions**:\n"
    "1. Enable **Mock Mode** if offline.\n"
    "2. Upload TVPM Excel file in **Validation Hub**.\n"
    "3. Fetch fields, map SoC path, and validate.\n"
    "4. Ask questions about specific TVPMs in the **Chat Assistant**."
)

# App Header
st.markdown('<div class="main-title">TVPM Validation Hub</div>', unsafe_allow_html=True)
st.markdown('<div class="subtitle">Extract TVPM IDs, query JIRA, and map SoC compatibility status in real-time.</div>', unsafe_allow_html=True)

# Create tabs
tab1, tab2 = st.tabs(["📊 Validation Hub", "💬 TVPM Chat Assistant"])

with tab1:
    uploaded_file = st.file_uploader("Choose your TVPM Excel file", type=["xlsx", "xls"])


    if uploaded_file is not None:
        # Read the file headers to let the user select columns
        try:
            # Load sheets just to read column names first
            file_bytes = uploaded_file.read()
            df_cols = load_excel_or_html(file_bytes)
            columns = df_cols.columns.tolist()
        
            # Reset file pointer
            uploaded_file.seek(0)
        except Exception as e:
            st.error(f"Error reading Excel columns: {e}")
            st.stop()
        
        st.markdown('<div class="card">', unsafe_allow_html=True)
        # Auto detect TVPM ID column
        default_index = 0
        jira_pattern = re.compile(r'^[A-Z0-9]+-\d+$')
        detected_col = None
        for col in columns:
            # Check the first 5 non-null rows of this column
            sample = df_cols[col].dropna().head(5).astype(str).tolist()
            if sample and all(jira_pattern.match(val.strip()) for val in sample):
                detected_col = col
                break
        
        if detected_col:
            default_index = columns.index(detected_col)
        else:
            # Fallback to column name matching (but avoid false positives like 'Side')
            detected = []
            for i, col in enumerate(columns):
                col_lower = str(col).lower()
                if 'tvpm' in col_lower or 'issue key' in col_lower:
                    detected.append((i, col))
                elif re.search(r'\bid\b|_id|id_', col_lower) and 'side' not in col_lower:
                    detected.append((i, col))
            if detected:
                default_index = detected[0][0]
        
        selected_column = st.selectbox(
            "Select TVPM ID Column",
            options=columns,
            index=default_index,
            help="Choose the column in your Excel sheet that contains the JIRA issue keys / TVPM IDs."
        )
        st.markdown('</div>', unsafe_allow_html=True)

        # SoC name configuration
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("### SoC Configuration")
        target_soc_model = st.text_input(
            "Target SoC Name",
            value="",
            placeholder="e.g. o26, k24",
            help="Enter the SoC model name (e.g. 'o26', 'k24') to evaluate. The corresponding JIRA field containing compatibility details will be auto-detected dynamically."
        )
        st.markdown('</div>', unsafe_allow_html=True)

        # Run Validation Button
        if st.button("🚀 Run TVPM Validation", type="primary", use_container_width=True):
            if not mock_mode and (not jira_url or not jira_pat):
                st.error("Please configure JIRA Base URL and PAT in the sidebar.")
            elif not target_soc_model.strip():
                st.error("Please enter a Target SoC Name to begin validation.")
            else:
                progress_bar = st.progress(0)
                status_text = st.empty()
                
                with st.spinner("Processing issues from JIRA API..."):
                    try:
                        uploaded_file.seek(0)
                        files = {"file": (uploaded_file.name, uploaded_file.read(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
                        data = {
                            "column_name": selected_column,
                            "soc_field": "", # Passed empty so backend dynamically auto-detects it
                            "soc_model": target_soc_model.strip(),
                            "jira_base_url": jira_url or "",
                            "jira_pat": jira_pat or "",
                            "mock": "true" if mock_mode else "false",
                            "use_ai": "true" if use_ai else "false",
                            "ollama_url": ollama_url,
                            "ollama_model": ollama_model
                        }

                    
                        status_text.text("Sending request to backend...")
                        progress_bar.progress(20)
                    
                        response = httpx.post(
                            f"{backend_url}/api/validate",
                            files=files,
                            data=data,
                            timeout=180.0 # High timeout for large sheets
                        )
                    
                        uploaded_file.seek(0) # Reset pointer
                    
                        if response.status_code == 200:
                            progress_bar.progress(100)
                            status_text.text("Validation completed!")
                        
                            # Load results Excel in memory
                            output_bytes = response.content
                            res_df = pd.read_excel(BytesIO(output_bytes))
                        
                            st.success("TVPM Validation completed successfully!")
                        
                            # Display summary metrics
                            total_rows = len(res_df)
                            applicable_count = len(res_df[res_df["Status"] == "Applicable"])
                            not_applicable_count = len(res_df[res_df["Status"] == "Not Applicable"])
                            error_count = len(res_df[res_df["Status"].str.startswith("Error")])
                        
                            m1, m2, m3, m4 = st.columns(4)
                            m1.metric("Total Issues", total_rows)
                            m2.metric("Applicable (O / THE)", applicable_count)
                            m3.metric("Not Applicable (X)", not_applicable_count)
                            m4.metric("Errors / Unresolved", error_count)
                        
                            # Download button
                            st.download_button(
                                label="📥 Download Validation Results Excel",
                                data=output_bytes,
                                file_name="TVPM_Validation_Results.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                use_container_width=True
                            )
                        
                            # Preview Table
                            st.markdown("### Results Preview (First 100 rows)")
                        
                            # Function to highlight status
                            def highlight_status(val):
                                if val == 'Applicable':
                                    return 'background-color: #D4EDDA; color: #155724;'
                                elif val == 'Not Applicable':
                                    return 'background-color: #F8D7DA; color: #721C24;'
                                elif str(val).startswith('Error'):
                                    return 'background-color: #FFF3CD; color: #856404;'
                                return ''
                            
                            styled_df = res_df.head(100).style.map(highlight_status, subset=['Status'])
                            st.dataframe(styled_df, use_container_width=True)
                        
                        else:
                            progress_bar.empty()
                            status_text.empty()
                            st.error(f"Validation failed: {response.json().get('detail')}")
                    except Exception as e:
                        progress_bar.empty()
                        status_text.empty()
                        st.error(f"Failed to complete validation. Error: {e}")
    else:
        # Premium landing page UI if no file is uploaded
        st.info("Please upload an Excel spreadsheet to begin validation.")
    
        st.markdown("""
        ### System Architecture & Work Flow
        This tool automates TVPM checking by querying the JIRA API directly:
        1. **Upload**: Drag and drop the Excel file containing your TVPM IDs.
        2. **Parse**: The FastAPI backend parses the sheets and extracts unique JIRA keys.
        3. **Query**: The tool makes concurrent, throttled HTTP requests to LGE JIRA.
        4. **Resolve**: Extracts the SoC details value (e.g. `'O'`, `'X'`, `'THE'`).
        5. **Map**: Applies validation mapping:
           * **'X'** $\rightarrow$ **Not Applicable**
           * **'O' / 'THE' / 'the'** $\rightarrow$ **Applicable**
           * Others $\rightarrow$ **Not Applicable**
        6. **Download**: Provides a downloadable `.xlsx` result table.
        """)
    
        # Showcase standard look
        st.subheader("Example Output Format")
        mock_data = pd.DataFrame([
            {"S.No": 1, "TVPM ID": "TVPM-1011", "SoC Details": "O", "Status": "Applicable"},
            {"S.No": 2, "TVPM ID": "TVPM-1012", "SoC Details": "X", "Status": "Not Applicable"},
            {"S.No": 3, "TVPM ID": "TVPM-1013", "SoC Details": "THE", "Status": "Applicable"},
            {"S.No": 4, "TVPM ID": "TVPM-1014", "SoC Details": "the", "Status": "Applicable"},
            {"S.No": 5, "TVPM ID": "TVPM-1015", "SoC Details": "None", "Status": "Not Applicable"}
        ])
        st.table(mock_data)


with tab2:
    st.markdown("### 💬 TVPM Chat Assistant")
    st.markdown("Select a JIRA issue from the local SQLite cache and ask natural language questions using Ollama.")
    
    # Query cached TVPMs from backend
    try:
        res = httpx.get(f"{backend_url}/api/cached-tvpms", timeout=5.0)
        if res.status_code == 200:
            cached_tvpms = res.json()
        else:
            cached_tvpms = []
    except Exception:
        cached_tvpms = []
        
    if not cached_tvpms:
        st.warning("⚠️ **No Cached Issues Found**")
        st.info("Please go to the **Validation Hub** tab, upload an Excel file, and run validation first to cache the issues in the database.")
    else:
        # Create select options
        options = [f"{item['key']} - {item['summary'][:50]}... ({item['status']})" for item in cached_tvpms]
        selected_option = st.selectbox("Select TVPM ID to chat with:", options=options)
        
        # Extract selected key
        selected_key = selected_option.split(" - ")[0]
        selected_issue = next(item for item in cached_tvpms if item["key"] == selected_key)
        
        # Display issue details card
        st.markdown(f'''
        <div class="card">
            <h4>{selected_issue['key']}: {selected_issue['summary']}</h4>
            <p><b>Cached Validation Status</b>: {selected_issue['status']}</p>
        </div>
        ''', unsafe_allow_html=True)
        
        # Initialize chat history for this specific key
        history_key = f"chat_history_{selected_key}"
        if history_key not in st.session_state:
            st.session_state[history_key] = []
            
        # Display chat messages
        for msg in st.session_state[history_key]:
            with st.chat_message(msg["role"]):
                st.write(msg["content"])
                
        # Chat input
        user_query = st.chat_input("Ask a question about this TVPM...")
        
        if user_query:
            # Display user message
            with st.chat_message("user"):
                st.write(user_query)
            st.session_state[history_key].append({"role": "user", "content": user_query})
            
            # Send request to backend
            with st.spinner("Ollama is thinking..."):
                try:
                    payload = {
                        "key": selected_key,
                        "question": user_query,
                        "ollama_url": ollama_url,
                        "ollama_model": ollama_model
                    }
                    chat_res = httpx.post(f"{backend_url}/api/chat-tvpm", json=payload, timeout=60.0)
                    if chat_res.status_code == 200:
                        response_text = chat_res.json().get("response", "")
                        with st.chat_message("assistant"):
                            st.write(response_text)
                        st.session_state[history_key].append({"role": "assistant", "content": response_text})
                    else:
                        error_detail = chat_res.json().get("detail", "Unknown error")
                        st.error(f"AI response failed: {error_detail}")
                except Exception as e:
                    st.error(f"Failed to communicate with AI chat endpoint: {e}")
            st.rerun()
