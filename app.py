import streamlit as st
import json
import os
from google import genai
from google.genai import errors
from core import extract_text_from_file, run_ai_cleaning_pipeline, chunk_markdown_text, check_and_update_quotas

# Set page configuration
st.set_page_config(
    page_title="RAG Knowledge Pipeline",
    page_icon="⚙️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom premium CSS styling injection
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap');
    
    /* Apply font styling */
    html, body, [class*="css"] {
        font-family: 'Outfit', sans-serif;
    }
    
    /* Premium Title Gradient */
    .title-gradient {
        background: linear-gradient(90deg, #3b82f6 0%, #8b5cf6 50%, #ec4899 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        font-size: 2.75rem;
        font-weight: 700;
        margin-bottom: 0.25rem;
    }
    
    /* Stat cards styling */
    .stat-card {
        background: rgba(31, 41, 55, 0.4);
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 10px;
        padding: 15px;
        text-align: center;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
    }
    
    .stat-val {
        font-size: 1.8rem;
        font-weight: 700;
        color: #3b82f6;
    }
    
    .stat-label {
        font-size: 0.85rem;
        color: #9ca3af;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }

    /* Enforce 50% max-width and centering for the file upload section above 1152px breakpoint */
    @media (min-width: 1152px) {
        .responsive-center-container,
        div[data-testid="stFileUploader"],
        div[data-testid="stElementContainer"] button[id^="initialize"] {
            max-width: 50% !important;
            margin: 0 auto !important;
        }
    }
    
    /* Elegant 16px explicit margins around the Promoted Download CTA container */
    .download-cta-wrapper {
        margin-top: 16px !important;
        margin-bottom: 16px !important;
        padding: 8px 0 !important;
    }
    
    /* Target and style the outer stCode container directly to match 500px height with border */
    div[data-testid="stCode"] {
        height: 500px !important;
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 8px;
        background-color: rgb(14, 17, 23);
        overflow: hidden !important;
    }
    
    div[data-testid="stCode"] pre {
        height: 100% !important;
        max-height: 500px !important;
        overflow-y: auto !important;
        white-space: pre-wrap !important; /* Avoid horizontal breaking layouts */
        margin: 0 !important;
        padding: 12px !important;
    }
</style>
""", unsafe_allow_html=True)

# ----------------- SESSION STATE SETUP -----------------
if "processed" not in st.session_state:
    st.session_state.processed = False
if "raw_text" not in st.session_state:
    st.session_state.raw_text = ""
if "cleaned_text" not in st.session_state:
    st.session_state.cleaned_text = ""
if "chunks" not in st.session_state:
    st.session_state.chunks = []
if "file_hash" not in st.session_state:
    st.session_state.file_hash = None

# Callback helper to update session state upon manual editor adjustments
def update_cleaned_content():
    if "editor" in st.session_state:
        st.session_state.cleaned_text = st.session_state.editor
        # Re-run semantic chunking on updated editor text to keep download current
        st.session_state.chunks = chunk_markdown_text(st.session_state.editor)

# ----------------- SIDEBAR (METADATA INPUT) -----------------
st.sidebar.markdown("<h2 style='margin-top: 0;'>Pipeline Metadata</h2>", unsafe_allow_html=True)

source_name = st.sidebar.text_input(
    "Source Document Name", 
    value="Unlabeled Source",
    help="Name of the original document or publication source."
)

custom_tags_raw = st.sidebar.text_input(
    "Custom Document Tags",
    value="",
    placeholder="UX, 2026, Guidelines, E-commerce",
    help="Flexible, domain-agnostic labels via comma-separated tokens."
)

# Parse comma-separated tags into a clean list
custom_tags = [tag.strip() for tag in custom_tags_raw.split(",") if tag.strip()]

st.sidebar.markdown("---")
st.sidebar.markdown("### Gemini API Configuration")

auth_mode = st.sidebar.radio(
    "Authentication Mode",
    options=["Use Default Key (Requires Password)", "Use My Personal API Key"],
    help="Select how you want to authenticate with the Gemini API."
)

is_authenticated = False
api_key = None
client = None

if auth_mode == "Use Default Key (Requires Password)":
    system_password = st.sidebar.text_input(
        "System Access Password",
        type="password",
        help="Enter the administrative password to use the default system key."
    )
    secret_key = st.secrets.get("SECRET_KEY")
    if not system_password:
        st.sidebar.warning("🔐 Please enter the system access password.")
    elif system_password != secret_key:
        st.sidebar.warning("🔐 Invalid or missing system access password.")
    else:
        api_key = st.secrets.get("GEMINI_API_KEY")
        if not api_key:
            st.sidebar.error("🚨 System default GEMINI_API_KEY is not configured in secrets.")
        else:
            is_authenticated = True
            try:
                client = genai.Client(api_key=api_key)
            except Exception as e:
                st.sidebar.error(f"🚨 Failed to initialize client: {str(e)}")
                is_authenticated = False
                
else: # Use My Personal API Key
    personal_key = st.sidebar.text_input(
        "Enter Personal Gemini API Key",
        type="password",
        help="Enter your personal Gemini API Key."
    )
    st.sidebar.markdown("[Get your own Gemini API Key here](https://ai.google.dev/gemini-api/docs/api-key?hl=fr)", unsafe_allow_html=True)
    
    if not personal_key:
        is_authenticated = False
    else:
        try:
            client = genai.Client(api_key=personal_key)
            # Live validation of key using client.models.list()
            client.models.list()
            is_authenticated = True
        except errors.APIError as api_err:
            st.sidebar.error("🚨 Personal API Key Validation Failed: Invalid token (401/404).")
            is_authenticated = False
            client = None
        except Exception as e:
            st.sidebar.error(f"🚨 Failed to initialize client: {str(e)}")
            is_authenticated = False
            client = None

target_model = st.sidebar.selectbox(
    "Target Model",
    options=["gemini-3.5-flash", "gemini-3.1-pro"],
    index=0,
    help="Select the Gemini model version to power the cleaning agent."
)

st.sidebar.markdown("---")
st.sidebar.markdown(
    """
    <div style='font-size: 0.8rem; color: #6b7280; text-align: center;'>
        RAG Knowledge Pipeline v1.0.0<br>
        Developed by Senior Full-Stack & AI Team
    </div>
    """,
    unsafe_allow_html=True
)

# ----------------- MAIN PANEL -----------------
st.markdown("<h1 class='title-gradient'>RAG Knowledge Pipeline</h1>", unsafe_allow_html=True)
st.markdown(
    "<p style='font-size: 1.1rem; color: #9ca3af; margin-bottom: 2rem;'>Intelligent ETL utility for parsing, optimizing, and chunking knowledge documents for Vector Database ingestion.</p>",
    unsafe_allow_html=True
)

# Visual placeholder card indicating AI Cleaning Pipeline status
st.markdown("""
<div style="background: linear-gradient(135deg, rgba(59, 130, 246, 0.08) 0%, rgba(139, 92, 246, 0.08) 100%);
            border: 1px solid rgba(59, 130, 246, 0.2);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 25px;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.15);
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);">
    <div style="display: flex; align-items: center; gap: 10px;">
        <span style="background-color: #059669; color: white; padding: 4px 10px; border-radius: 9999px; font-size: 0.72rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; display: inline-flex; align-items: center; gap: 6px;">
            <span style="width: 6px; height: 6px; background-color: #34d399; border-radius: 50%; display: inline-block; animation: pulse 1.8s infinite;"></span>
            ACTIVE
        </span>
        <h4 style="margin: 0; color: #e5e7eb; font-size: 0.95rem; font-weight: 600;">AI Optimization & Noise Vetting Engine</h4>
    </div>
    <p style="margin: 8px 0 0 0; color: #9ca3af; font-size: 0.85rem; line-height: 1.5;">
        All uploaded files are routed through our active, domain-agnostic LLM cleaning pipeline. This universal noise-filtering agent strips structural headers/footers, metadata noise, formatting inconsistencies, copyright notices, and generic filler text from any document style.
    </p>
</div>
<style>
@keyframes pulse {
    0% { transform: scale(0.85); opacity: 1; }
    50% { transform: scale(1.2); opacity: 0.3; }
    100% { transform: scale(0.85); opacity: 1; }
}
</style>
""", unsafe_allow_html=True)

# Responsive container start for uploader and CTA button
st.markdown('<div class="responsive-center-container">', unsafe_allow_html=True)

# Document uploader
uploaded_file = st.file_uploader(
    "Upload Source Document", 
    type=["pdf", "md", "txt", "docx", "pptx", "csv", "xlsx", "html", "htm"], 
    help="Upload PDF, MD, TXT, DOCX, PPTX, CSV, XLSX, HTML, or HTM source file to initialize ingest sequence."
)

# Handle file change and reset state appropriately
if uploaded_file:
    current_file_id = f"{uploaded_file.name}_{uploaded_file.size}"
    if st.session_state.file_hash != current_file_id:
        st.session_state.processed = False
        st.session_state.raw_text = ""
        st.session_state.cleaned_text = ""
        st.session_state.chunks = []
        st.session_state.file_hash = current_file_id
else:
    st.session_state.processed = False
    st.session_state.raw_text = ""
    st.session_state.cleaned_text = ""
    st.session_state.chunks = []
    st.session_state.file_hash = None

# Action CTA Button and Spinner Container
st.markdown("<div style='margin-top: 1rem;'></div>", unsafe_allow_html=True)
button_placeholder = st.empty()
spinner_placeholder = st.empty()

# Render button disabled if no file is present or authentication is missing
start_processing = False
if not is_authenticated:
    button_placeholder.button(
        "Authentication required to start", 
        disabled=True, 
        use_container_width=True
    )
elif uploaded_file:
    start_processing = button_placeholder.button(
        "Initialize Document Optimization & Chunking", 
        type="primary",
        use_container_width=True
    )
else:
    button_placeholder.button(
        "Upload a document to start", 
        disabled=True, 
        use_container_width=True
    )

st.markdown('</div>', unsafe_allow_html=True)

# ----------------- PIPELINE EXECUTION -----------------
if start_processing and uploaded_file:
    # Ensure is_authenticated evaluates to True
    if not is_authenticated or not client:
        st.error("Authentication failed. Please verify your password or API key in the sidebar.")
        st.stop()
        
    with spinner_placeholder.container():
        try:
            # Step 1: Text extraction
            with st.spinner("Extracting text from file..."):
                raw_extracted = extract_text_from_file(uploaded_file)
                st.session_state.raw_text = raw_extracted
            
            # Local token rate-limiting and budgeting check (bypass if personal key is used)
            if auth_mode == "Use Default Key (Requires Password)":
                estimated_tokens = int(len(raw_extracted.split()) * 1.3)
                allowed, quota_msg = check_and_update_quotas("local_user_default", estimated_tokens)
                if not allowed:
                    st.error(quota_msg)
                    st.stop()
            
            # Step 2: AI Cleaning with progress update
            progress_bar = st.progress(0.0)
            status_text = st.empty()
            
            def update_progress(current, total):
                pct = float(current) / float(total)
                progress_bar.progress(pct)
                status_text.markdown(f"**Processing Block {current} of {total} via Gemini...**")

            with st.spinner("Executing AI-powered text cleaning & optimization..."):
                cleaned_output = run_ai_cleaning_pipeline(
                    raw_extracted,
                    client,
                    target_model,
                    progress_callback=update_progress
                )
                st.session_state.cleaned_text = cleaned_output
            
            # Clear progress indicator
            progress_bar.empty()
            status_text.empty()
            
            # Step 3: Semantic Chunking
            with st.spinner("Executing semantic Markdown chunking..."):
                chunks = chunk_markdown_text(cleaned_output)
                st.session_state.chunks = chunks
            
            st.session_state.processed = True
            st.toast("Document parsed, optimized, and chunked successfully!", icon="✅")
            
        except Exception as e:
            st.error(f"Pipeline Execution Failed: {str(e)}")
            st.session_state.processed = False

# ----------------- DISPLAY RESULTS AND EXPORT -----------------
if st.session_state.processed:
    st.markdown("---")
    
    # Execution Metrics Summary Row
    m_col1, m_col2, m_col3 = st.columns(3)
    
    total_words = len(st.session_state.cleaned_text.split())
    total_chars = len(st.session_state.cleaned_text)
    num_chunks = len(st.session_state.chunks)
    
    with m_col1:
        st.markdown(
            f"<div class='stat-card'><div class='stat-val'>{num_chunks}</div><div class='stat-label'>Chunks Generated</div></div>", 
            unsafe_allow_html=True
        )
    with m_col2:
        st.markdown(
            f"<div class='stat-card'><div class='stat-val'>{total_words:,}</div><div class='stat-label'>Optimized Words</div></div>", 
            unsafe_allow_html=True
        )
    with m_col3:
        st.markdown(
            f"<div class='stat-card'><div class='stat-val'>{total_chars:,}</div><div class='stat-label'>Total Characters</div></div>", 
            unsafe_allow_html=True
        )
        
    # Prepare Unified JSON Structure
    export_payload = {
        "metadata": {
            "source": source_name,
            "tags": custom_tags,
            "file_name": uploaded_file.name,
            "original_size_bytes": uploaded_file.size
        },
        "stats": {
            "total_chunks": len(st.session_state.chunks),
            "total_words": len(st.session_state.cleaned_text.split()),
            "total_characters": len(st.session_state.cleaned_text)
        },
        "chunks": st.session_state.chunks
    }
    json_output = json.dumps(export_payload, indent=2)

    # Full-width Promoted Download CTA inside styled spacing container
    st.markdown('<div class="download-cta-wrapper">', unsafe_allow_html=True)
    st.download_button(
        label="Download structured JSON",
        data=json_output,
        file_name=f"{source_name.lower().replace(' ', '_')}_vector_payload.json",
        mime="application/json",
        use_container_width=True
    )
    st.markdown('</div>', unsafe_allow_html=True)
    
    # Split Layout for Editing & Final JSON review
    col_editor, col_preview = st.columns([1, 1])
    
    with col_editor:
        st.subheader("📝 Preview & Edit Optimized Markdown")
        st.markdown(
            "<p style='color: #6b7280; font-size: 0.85rem;'>Adjust cleaned content below to perfect headers and hierarchy. Chunk segmentation updates instantly.</p>", 
            unsafe_allow_html=True
        )
        
        # Streamlit text area mapped to session state and trigger chunking on change
        st.text_area(
            "Cleaned Markdown Content",
            value=st.session_state.cleaned_text,
            height=500,
            key="editor",
            label_visibility="collapsed",
            on_change=update_cleaned_content
        )
        
    with col_preview:
        st.subheader("📦 Generated Vector Ingest JSON")
        st.markdown(
            "<p style='color: #6b7280; font-size: 0.85rem;'>Structured JSON payload ready for Vector Store embedding ingestion.</p>", 
            unsafe_allow_html=True
        )
        
        # Display formatted code directly (scrolling and borders are handled by global CSS overrides)
        st.code(json_output, language="json", line_numbers=True)

    # Accordion detail list for chunk diagnostics
    with st.expander("🔍 Detailed Chunk Segments Diagnostic"):
        for idx, chk in enumerate(st.session_state.chunks):
            st.markdown(f"**Chunk #{idx + 1}** | Section Path: `{chk['header_path']}`")
            st.markdown(f"*Words: {chk['word_count']}*")
            st.text(chk['content'])
            st.markdown("---")
