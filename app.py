import os
import requests
import streamlit as st
from dotenv import load_dotenv

try:
    import markdown  # python-markdown (supports tables with 'tables' extension)

    def md_to_html(md_text: str) -> str:
        return markdown.markdown(
            md_text, extensions=["extra", "tables", "sane_lists"], output_format="html5"
        )

except ModuleNotFoundError:
    # Fallback to markdown2 (ensure it's in requirements.txt) – supports tables via 'tables' extra
    import markdown2

    def md_to_html(md_text: str) -> str:
        return markdown2.markdown(md_text, extras=["tables", "fenced-code-blocks"])


from weasyprint import HTML

# ------------------ CONFIG ------------------
load_dotenv()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o")
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")
PERPLEXITY_MODEL = os.getenv("PERPLEXITY_MODEL", "sonar-pro")
OPENROUTER_RATE = float(os.getenv("OPENROUTER_RATE_PER_1K", "0.005"))
PERPLEXITY_RATE = float(os.getenv("PERPLEXITY_RATE_PER_1K", "0.01"))

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
st.set_page_config(page_title="B2B Firmenanalyse", layout="wide")
st.title("🔍 B2B Kundenanalyse Generator")

period_options = {
    "Letzter Tag": "day",
    "Letzte Woche": "week",
    "Letzter Monat": "month",
    "Letztes Jahr": "year",
    "Alle Zeiträume": "all",
}

# Show waiting message directly under the title when analysis is running
if st.session_state.analysis_running:
    st.markdown("### ⏳ Bitte warten... Analyse dauert einige Minuten")
    st.info("Sie können die Analyse jederzeit mit dem Stoppen-Button abbrechen.")

with st.sidebar:
    prompt_choice = st.selectbox(
        "Analyse-Typ", ["Firmenanalyse", "Absatzprofil"], key="prompt_choice"
    )
    period_label = st.selectbox(
        "Analysezeitraum",
        list(period_options.keys()),
        index=list(period_options).index("Alle Zeiträume"),
        key="period",
    )
    search_period = period_options[period_label]

    # Clear results when user changes analysis type or time period
    if "last_prompt_choice" not in st.session_state:
        st.session_state.last_prompt_choice = prompt_choice
        st.session_state.last_period_label = period_label
    
    # Check if user changed the selection
    if (st.session_state.last_prompt_choice != prompt_choice or 
        st.session_state.last_period_label != period_label):
        # Clear previous results
        st.session_state.analysis_markdown = ""
        st.session_state.token_info = ""
        st.session_state.analysis_title = ""
        st.session_state.analysis_running = False
        st.session_state.analysis_cancelled = False
        st.session_state.cancel_message = ""
        # Update stored values
        st.session_state.last_prompt_choice = prompt_choice
        st.session_state.last_period_label = period_label

    if prompt_choice == "Firmenanalyse":
        user_input = st.text_input("Unternehmensname", key="company")
    else:
        user_input = st.text_input(
            "Produktbeschreibung",
            key="product",
        )

    start_btn = st.button("🚀 Analyse starten")
    
    # Cancel button (always visible, but only functional when analysis is running)
    cancel_btn = st.button("⏹️ Analyse stoppen", type="secondary", disabled=not st.session_state.analysis_running)
    if cancel_btn and st.session_state.analysis_running:
        st.session_state.analysis_cancelled = True
        st.session_state.analysis_running = False
        st.session_state.cancel_message = "Analyse gestoppt"

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
    st.session_state.current_search_period = search_period
    
    # Just set the state and rerun - don't show anything here to avoid conflicts
    st.rerun()

# ---------- RUN ANALYSIS ----------
if st.session_state.analysis_running and not st.session_state.analysis_cancelled:
    try:
        # Build the research prompt
        prompt_template = prompts[st.session_state.current_prompt_choice]
        full_prompt = prompt_template.format(
            company_name=st.session_state.current_user_input, product_description=st.session_state.current_user_input
        )

        # Check for cancellation before starting Perplexity API call
        if st.session_state.analysis_cancelled:
            st.session_state.analysis_running = False
            st.rerun()
            
        # ---- Perplexity (Recherche) ----
        px_headers = {
            "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
            "Content-Type": "application/json",
        }
        px_payload = {
            "model": PERPLEXITY_MODEL,
            "messages": [{"role": "user", "content": full_prompt}],
            "search_scope": "recent",
            "search_period": st.session_state.current_search_period,
        }
        px_resp = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers=px_headers,
            json=px_payload,
        )
        px_resp.raise_for_status()
        px_json = px_resp.json()
        raw_text = px_json["choices"][0]["message"]["content"].strip()
        px_tokens = px_json.get("usage", {}).get("total_tokens", 0)
        
        # Check for cancellation after Perplexity API call
        if st.session_state.analysis_cancelled:
            st.session_state.analysis_running = False
            st.rerun()

        # ---- Zitat‑URLs anhängen ----
        citations = px_json.get("citations", None)
        if isinstance(citations, list):
            links = [
                c.get("url")
                for c in citations
                if isinstance(c, dict) and c.get("url")
            ]
            if links:
                raw_text = raw_text.replace("\u001b", "").replace("\x1b", "")
                raw_text += "\n\n### Webseiten-Quellen:\n" + "\n".join(
                    f"- [{u}]({u})" for u in links
                )

        # Check for cancellation before OpenRouter API call
        if st.session_state.analysis_cancelled:
            st.session_state.analysis_running = False
            st.rerun()
            
        # ---- GPT‑4o (Formatierung) ----
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
{raw_text}
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
        or_resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=or_headers,
            json=or_payload,
        )
        or_resp.raise_for_status()
        or_json = or_resp.json()
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
        
        # Analysis completed successfully
        st.session_state.analysis_running = False

    except Exception as e:
        st.session_state.analysis_running = False
        if not st.session_state.analysis_cancelled:
            st.error(f"Analyse fehlgeschlagen: {e}")
        else:
            st.session_state.cancel_message = "Analyse gestoppt"

# ---------- DISPLAY RESULTS ----------
# Show cancellation message if analysis was stopped
if st.session_state.cancel_message:
    st.warning(st.session_state.cancel_message)
    st.session_state.cancel_message = ""  # Clear the message after showing

# Show results only when analysis is not running
if st.session_state.analysis_markdown and not st.session_state.analysis_running:
    st.markdown("---")
    st.markdown(f"### Analyse für: {st.session_state.analysis_title}")
    st.markdown(st.session_state.analysis_markdown, unsafe_allow_html=True)
    st.sidebar.markdown("---")
    st.sidebar.markdown(st.session_state.token_info)

    # --- Markdown -> HTML mit Tabellensupport ---
    html_body = md_to_html(st.session_state.analysis_markdown)

    html_template = f"""
    <html><head><meta charset='utf-8'><style>
        @page {{ size: A4 portrait; margin: 1.8cm; }}
        body {{ font-family: Arial, sans-serif; font-size: 10pt; line-height: 1.45; }}
        h1,h2,h3 {{ color: #222; margin-top: 1.2em; }}
        table {{ width: 100%; table-layout: fixed; border-collapse: collapse; font-size: 9pt; margin: 0.8em 0; }}
        th,td {{ border: 1px solid #ccc; padding: 4px 6px; text-align: left; word-wrap: break-word; overflow-wrap: anywhere; hyphens: auto; }}
        a {{ color: #0044cc; text-decoration: none; }}
    </style></head><body>
    {html_body}
    </body></html>"""
    try:
        pdf_data = HTML(string=html_template).write_pdf()
        st.download_button(
            "📄 Analyse als PDF herunterladen",
            pdf_data,
            file_name=f"Analyse_{st.session_state.analysis_title.replace(' ', '_')}.pdf",
            mime="application/pdf",
        )
    except Exception as e:
        st.error(f"PDF‑Erstellung fehlgeschlagen: {e}")
