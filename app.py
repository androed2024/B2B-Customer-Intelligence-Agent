import os
import requests
import streamlit as st
import sqlite3
import pandas as pd
import base64
from datetime import datetime
from dotenv import load_dotenv
import yaml
import streamlit_authenticator as stauth

try:
    import markdown  # python-markdown (supports tables with 'tables' extension)

    # Try to import linkify extension for auto-linking plain URLs
    try:
        import markdown_linkify

        linkify_available = True
    except ImportError:
        linkify_available = False

    def md_to_html(md_text: str) -> str:
        extensions = ["extra", "tables", "sane_lists"]
        if linkify_available:
            extensions.append("linkify")
        return markdown.markdown(md_text, extensions=extensions, output_format="html5")

except ModuleNotFoundError:
    # Fallback to markdown2 (ensure it's in requirements.txt) – supports tables via 'tables' extra
    import markdown2

    def md_to_html(md_text: str) -> str:
        return markdown2.markdown(
            md_text, extras=["tables", "fenced-code-blocks", "autolink"]
        )


from weasyprint import HTML

# ------------------ CONFIG ------------------
load_dotenv()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-2024-11-20")
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")
PERPLEXITY_MODEL = os.getenv("PERPLEXITY_MODEL", "sonar-pro")
OPENROUTER_RATE = float(os.getenv("OPENROUTER_RATE_PER_1K", "0.005"))
PERPLEXITY_RATE = float(os.getenv("PERPLEXITY_RATE_PER_1K", "0.01"))
AUTH_YAML_RAW = os.getenv("AUTH_CONFIG_YAML", "")
if not AUTH_YAML_RAW:
    auth_path = os.getenv("AUTH_CONFIG_PATH")
    if auth_path and os.path.exists(auth_path):
        with open(auth_path, "r", encoding="utf-8") as f:
            AUTH_YAML_RAW = f.read()

if not AUTH_YAML_RAW:
    st.error("Auth-Config fehlt (AUTH_CONFIG_YAML/ AUTH_CONFIG_PATH).")
    st.stop()

config = yaml.safe_load(AUTH_YAML_RAW)

# ---------- Database Functions ----------


def init_database():
    """Initialize SQLite database for analysis history"""
    conn = sqlite3.connect("analysis_history.db")
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            analysis_type TEXT NOT NULL,
            input_value TEXT NOT NULL,
            perplexity_tokens INTEGER DEFAULT 0,
            openrouter_tokens INTEGER DEFAULT 0,
            total_cost_eur REAL DEFAULT 0.0,
            pdf_data BLOB,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """
    )

    # Add new columns if they don't exist (for backward compatibility)
    try:
        c.execute("ALTER TABLE analyses ADD COLUMN perplexity_tokens INTEGER DEFAULT 0")
    except:
        pass
    try:
        c.execute("ALTER TABLE analyses ADD COLUMN openrouter_tokens INTEGER DEFAULT 0")
    except:
        pass
    try:
        c.execute("ALTER TABLE analyses ADD COLUMN total_cost_eur REAL DEFAULT 0.0")
    except:
        pass
    try:
        c.execute("ALTER TABLE analyses ADD COLUMN pdf_data BLOB")
    except:
        pass

    # Clear existing data for fresh start (remove this after first deployment)
    # c.execute('DELETE FROM analyses')  # Commented out to preserve data

    conn.commit()
    conn.close()


def log_analysis(
    analysis_type, input_value, px_tokens, or_tokens, total_cost_eur, pdf_data
):
    """Log a completed analysis to the database"""
    conn = sqlite3.connect("analysis_history.db")
    c = conn.cursor()
    now = datetime.now()
    date = now.strftime("%Y-%m-%d")
    time = now.strftime("%H:%M:%S")

    c.execute(
        """
        INSERT INTO analyses (date, time, analysis_type, input_value, perplexity_tokens, openrouter_tokens, total_cost_eur, pdf_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            date,
            time,
            analysis_type,
            input_value,
            px_tokens,
            or_tokens,
            total_cost_eur,
            pdf_data,
        ),
    )
    conn.commit()
    conn.close()


def get_analysis_history():
    """Get all analysis history from database"""
    conn = sqlite3.connect("analysis_history.db")
    df = pd.read_sql_query(
        """
        SELECT id, date, time, analysis_type, input_value
        FROM analyses 
        ORDER BY id DESC
    """,
        conn,
    )
    conn.close()
    return df


def get_total_stats():
    """Get total tokens and costs from database"""
    conn = sqlite3.connect("analysis_history.db")
    c = conn.cursor()
    c.execute(
        """
        SELECT 
            COALESCE(SUM(perplexity_tokens), 0) as total_px_tokens,
            COALESCE(SUM(openrouter_tokens), 0) as total_or_tokens,
            COALESCE(SUM(total_cost_eur), 0) as total_cost_eur
        FROM analyses
    """
    )
    result = c.fetchone()
    conn.close()

    if result:
        total_tokens = result[0] + result[1]
        total_cost = result[2]
        return total_tokens, total_cost
    return 0, 0.0


def get_pdf_from_db(analysis_id):
    """Get PDF data from database by analysis ID"""
    conn = sqlite3.connect("analysis_history.db")
    c = conn.cursor()
    c.execute("SELECT pdf_data FROM analyses WHERE id = ?", (analysis_id,))
    result = c.fetchone()
    conn.close()

    if result and result[0]:
        return result[0]
    return None


# ---------- Helper to load prompt templates ----------


def load_prompts():
    namespace = {}
    with open("prompts.txt", "r", encoding="utf-8") as f:
        exec(f.read(), namespace)
    return {
        "Firmenanalyse": namespace.get("research_prompt1", ""),
        "Absatzprofil": namespace.get("research_prompt2", ""),
    }


prompts = load_prompts()

# ---------- DATABASE INITIALIZATION ----------
init_database()

# ---------- SESSION STATE INITIALIZATION ----------
if "analysis_markdown" not in st.session_state:
    st.session_state.analysis_markdown = ""
    st.session_state.token_info = ""
    st.session_state.analysis_title = ""
    st.session_state.analysis_running = False
    st.session_state.analysis_cancelled = False
    st.session_state.cancel_message = ""
    st.session_state.current_user_input = ""
    st.session_state.current_prompt_choice = ""
    st.session_state.current_search_period = "all"
    st.session_state.last_prompt_choice = ""
    st.session_state.last_period_label = ""

# ---------- UI SETUP ----------
st.set_page_config(page_title="B2B KI-Research & Analyse", layout="wide")

# Reduce top padding with custom CSS and enlarge tabs
st.markdown(
    """
<style>
.block-container {
    padding-top: 3rem;
}
/* Make tabs larger */
.stTabs [data-baseweb="tab-list"] {
    gap: 20px;
}
.stTabs [data-baseweb="tab"] {
    font-size: 18px;
    font-weight: 600;
    padding: 12px 24px;
}
</style>
""",
    unsafe_allow_html=True,
)

# Header with title left, stats center/right, and version right
total_tokens, total_cost = get_total_stats()

col1, col2, col3 = st.columns([3, 2, 1])

with col1:
    st.markdown("### 🔍 B2B KI-Research & Analyse")

with col2:
    if total_tokens > 0 or total_cost > 0:
        st.markdown("")  # Empty line for spacing
        st.markdown(
            f"🔢 Tokens: {total_tokens:,}  /  💸 Ges.Kosten: {total_cost:.2f} €"
        )

with col3:
    st.markdown("")  # Empty line for spacing
    st.markdown("*Version: 1.1*")

period_options = {
    "Letzter Tag": "day",
    "Letzte Woche": "week",
    "Letzter Monat": "month",
    "Letztes Jahr": "year",
    "Alle Zeiträume": "all",
}

# Main menu tabs
tab1, tab2 = st.tabs(["🆕 Neue Analyse", "📊 Analyse Historie"])

with tab1:
    # Input fields directly in the tab
    col1, col2 = st.columns(2)

    with col1:
        prompt_choice = st.selectbox(
            "Analyse-Typ", ["Firmenanalyse", "Absatzprofil"], key="prompt_choice"
        )

    with col2:
        period_label = st.selectbox(
            "Analysezeitraum",
            [
                "Letzter Tag",
                "Letzte Woche",
                "Letzter Monat",
                "Letztes Jahr",
                "Alle Zeiträume",
            ],
            index=4,
            key="period_choice",
        )

    # Input field based on analysis type
    if prompt_choice == "Firmenanalyse":
        user_input = st.text_input("Unternehmensname", key="company")
    else:
        user_input = st.text_input("Produktbeschreibung", key="product")

    # Buttons side by side
    col_btn1, col_btn2 = st.columns(2)

    with col_btn1:
        start_btn = st.button("🚀 Analyse starten", use_container_width=True)

    with col_btn2:
        cancel_btn = st.button(
            "⏹️ Analyse stoppen",
            disabled=not st.session_state.analysis_running,
            use_container_width=True,
        )

    # Handle cancel button click
    if cancel_btn and st.session_state.analysis_running:
        st.session_state.analysis_cancelled = True
        st.session_state.analysis_running = False
        st.session_state.cancel_message = "Analyse gestoppt"
        st.rerun()

    # Show waiting message only when analysis is running AND no results yet
    if st.session_state.analysis_running and not st.session_state.analysis_markdown:
        st.info("⏳ Bitte warten... Analyse dauert einige Minuten")
        st.info("Sie können die Analyse jederzeit mit dem Stoppen-Button abbrechen.")

    # Show cancellation message if analysis was stopped
    if st.session_state.cancel_message:
        st.warning(st.session_state.cancel_message)
        st.session_state.cancel_message = ""  # Clear the message after showing

    # Show results only when analysis is not running
    if st.session_state.analysis_markdown and not st.session_state.analysis_running:
        st.markdown("---")
        st.markdown(f"### Analyse für: {st.session_state.analysis_title}")
        st.markdown(st.session_state.analysis_markdown, unsafe_allow_html=True)

        # --- Markdown -> HTML mit Tabellensupport ---
        html_body = md_to_html(st.session_state.analysis_markdown)

        html_template = f"""
        <html><head><meta charset='utf-8'><style>
            @page {{ size: A4 portrait; margin: 1.8cm; }}
            body {{ font-family: Arial, sans-serif; font-size: 10pt; line-height: 1.45; }}
            h1,h2,h3 {{ color: #222; margin-top: 1.2em; }}
            table {{ width: 100%; table-layout: fixed; border-collapse: collapse; font-size: 9pt; margin: 0.8em 0; }}
            th,td {{ border: 1px solid #ccc; padding: 4px 6px; text-align: left; word-wrap: break-word; overflow-wrap: anywhere; hyphens: auto; }}
            a {{ color: #0044cc; text-decoration: underline; }}
            a:visited {{ color: #660099; }}
        </style></head><body>
        {html_body}
        </body></html>"""
        try:
            pdf_data = HTML(string=html_template, base_url=".").write_pdf()
            st.download_button(
                "📄 Analyse als PDF herunterladen",
                pdf_data,
                file_name=f"Analyse_{st.session_state.analysis_title.replace(' ', '_')}.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
        except Exception as e:
            st.error(f"PDF-Generierung fehlgeschlagen: {e}")

    # ---------- HANDLE ANALYSIS START ----------
    if start_btn and user_input.strip():
        # Clear previous results and reset cancellation state
        st.session_state.analysis_markdown = ""
        st.session_state.token_info = ""
        st.session_state.analysis_title = ""
        st.session_state.analysis_cancelled = False
        st.session_state.analysis_running = True
        st.session_state.cancel_message = ""
        st.session_state.current_user_input = user_input
        st.session_state.current_prompt_choice = prompt_choice
        st.session_state.current_search_period = period_options[period_label]
        st.rerun()

    # ---------- RUN ANALYSIS ----------
    if st.session_state.analysis_running and not st.session_state.analysis_cancelled:
        try:
            prompts = load_prompts()

            if st.session_state.current_prompt_choice == "Firmenanalyse":
                perplexity_prompt = prompts["Firmenanalyse"].format(
                    company_name=st.session_state.current_user_input
                )
            else:
                perplexity_prompt = prompts["Absatzprofil"].format(
                    product_description=st.session_state.current_user_input
                )

            if st.session_state.analysis_cancelled:
                st.session_state.analysis_running = False
                st.rerun()

            # ---- PERPLEXITY SEARCH ----
            px_headers = {
                "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
                "Content-Type": "application/json",
            }

            # ---- Perplexity (Recherche) ---- (Back to original working version)
            px_payload = {
                "model": PERPLEXITY_MODEL,
                "messages": [{"role": "user", "content": perplexity_prompt}],
                "search_scope": "recent",
                "search_period": st.session_state.current_search_period,
            }

            # API call in progress (debug info removed)

            px_response = requests.post(
                "https://api.perplexity.ai/chat/completions",
                headers=px_headers,
                json=px_payload,
            )
            px_response.raise_for_status()
            px_json = px_response.json()
            px_content = px_json["choices"][0]["message"]["content"].strip()
            px_tokens = px_json.get("usage", {}).get("total_tokens", 0)

            # ---- Zitat‑URLs anhängen ----
            citations = px_json.get("citations", None)
            if isinstance(citations, list):
                links = [
                    c.get("url")
                    for c in citations
                    if isinstance(c, dict) and c.get("url")
                ]
                if links:
                    px_content = px_content.replace("\u001b", "").replace("\x1b", "")
                    px_content += "\n\n### Webseiten-Quellen:\n" + "\n".join(
                        f"- [{u}]({u})" for u in links
                    )

            if st.session_state.analysis_cancelled:
                st.session_state.analysis_running = False
                st.session_state.cancel_message = "Analyse gestoppt"
                st.rerun()
            else:
                # Continue with OpenRouter analysis only if Perplexity was successful

                # ---- GPT‑4o (Formatierung) ---- (Back to original working version)
                format_prompt = f"""
Du bist ein erfahrener Redakteur und Markdown‑Profi.

### Aufgabe:
Formatiere den folgenden Text als einheitliches, elegantes Markdown‑Dokument mit:
- Überschriften (## / ###)
- Bulletpoints, wo sinnvoll
- echten Markdown‑Tabellen
- **kein** HTML, **keine** ASCII‑Grafik, **keine** Farben

### Text:
---
{px_content}
"""
                or_headers = {
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                }
                or_payload = {
                    "model": OPENROUTER_MODEL,
                    "messages": [
                        {"role": "system", "content": "Formatierungs‑Experte"},
                        {"role": "user", "content": format_prompt},
                    ],
                }

                or_response = requests.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=or_headers,
                    json=or_payload,
                )
                or_response.raise_for_status()
                or_json = or_response.json()
                analysis_md = or_json["choices"][0]["message"]["content"].strip()
                or_tokens = or_json.get("usage", {}).get("total_tokens", 0)

                # ---- Kosteninfo ----
                cost_px = round(px_tokens / 1000 * PERPLEXITY_RATE, 4)
                cost_or = round(or_tokens / 1000 * OPENROUTER_RATE, 4)
                total_usd = cost_px + cost_or
                eur_rate = 0.92  # Approx. conversion rate USD → EUR
                total_eur = round(total_usd * eur_rate, 4)
                token_info = (
                    "🔢 Tokens\n"
                    f"- Perplexity: {px_tokens}\n"
                    f"- OpenRouter: {or_tokens}\n"
                    f"\n"
                    f"💸 Ges.Kosten:\n"
                    f"- {total_usd:.4f} USD / {total_eur:.4f} EUR"
                )

                # ---- Session speichern ----
                st.session_state.analysis_markdown = analysis_md
                st.session_state.token_info = token_info
                st.session_state.analysis_title = st.session_state.current_user_input
                st.session_state.analysis_running = (
                    False  # Stop analysis when results are ready
                )

                # Generate PDF for database storage
                try:
                    html_body = md_to_html(analysis_md)
                    html_template = f"""
                    <html><head><meta charset='utf-8'><style>
                        @page {{ size: A4 portrait; margin: 1.8cm; }}
                        body {{ font-family: Arial, sans-serif; font-size: 10pt; line-height: 1.45; }}
                        h1,h2,h3 {{ color: #222; margin-top: 1.2em; }}
                        table {{ width: 100%; table-layout: fixed; border-collapse: collapse; font-size: 9pt; margin: 0.8em 0; }}
                        th,td {{ border: 1px solid #ccc; padding: 4px 6px; text-align: left; word-wrap: break-word; overflow-wrap: anywhere; hyphens: auto; }}
                        a {{ color: #0044cc; text-decoration: underline; }}
                        a:visited {{ color: #660099; }}
                    </style></head><body>
                    {html_body}
                    </body></html>"""
                    pdf_data = HTML(string=html_template, base_url=".").write_pdf()
                except:
                    pdf_data = None

                # Log successful analysis to database
                log_analysis(
                    st.session_state.current_prompt_choice,
                    st.session_state.current_user_input,
                    px_tokens,
                    or_tokens,
                    total_eur,
                    pdf_data,
                )

                # Analysis completed successfully
                st.session_state.analysis_running = False
                st.rerun()

        except Exception as e:
            if not st.session_state.analysis_cancelled:
                st.error(f"Analyse fehlgeschlagen: {e}")
            else:
                st.session_state.cancel_message = "Analyse gestoppt"
            st.session_state.analysis_running = False
            st.rerun()

with tab2:
    # Clear previous analysis results when switching to history tab
    if st.session_state.analysis_markdown:
        st.session_state.analysis_markdown = ""
        st.session_state.token_info = ""
        st.session_state.analysis_title = ""
        st.session_state.analysis_running = False

    try:
        history_df = get_analysis_history()

        if len(history_df) > 0 and all(
            col in history_df.columns
            for col in ["id", "date", "time", "analysis_type", "input_value"]
        ):
            # Create custom table with clickable PDF icons
            # Header row
            col_pdf, col_id, col_datum, col_zeit, col_typ, col_eingabe = st.columns(
                [0.5, 0.5, 1, 1, 1.2, 3]
            )

            with col_pdf:
                st.markdown("**PDF**")
            with col_id:
                st.markdown("**ID**")
            with col_datum:
                st.markdown("**Datum**")
            with col_zeit:
                st.markdown("**Uhrzeit**")
            with col_typ:
                st.markdown("**Analysetyp**")
            with col_eingabe:
                st.markdown("**Eingabe**")

            st.markdown("---")

            # Data rows
            for _, row in history_df.iterrows():
                analysis_id = row["id"]
                pdf_data = get_pdf_from_db(analysis_id)

                col_pdf, col_id, col_datum, col_zeit, col_typ, col_eingabe = st.columns(
                    [0.5, 0.5, 1, 1, 1.2, 3]
                )

                with col_pdf:
                    if pdf_data:
                        st.markdown(
                            f"""<a href="data:application/pdf;base64,{base64.b64encode(pdf_data).decode()}" download="Analyse_{analysis_id}_{row['analysis_type']}_{row['input_value'][:20].replace(' ', '_')}.pdf">
                                <small>📄</small>
                            </a>""",
                            unsafe_allow_html=True,
                        )
                    else:
                        st.write("-")

                with col_id:
                    st.write(str(analysis_id))
                with col_datum:
                    st.write(row["date"])
                with col_zeit:
                    st.write(row["time"])
                with col_typ:
                    st.write(row["analysis_type"])
                with col_eingabe:
                    st.write(
                        row["input_value"][:80] + "..."
                        if len(row["input_value"]) > 80
                        else row["input_value"]
                    )

        else:
            st.info("Noch keine Analysen durchgeführt.")

    except Exception as e:
        st.error(f"Fehler beim Laden der Historie: {e}")
