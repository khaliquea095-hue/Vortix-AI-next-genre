"""
Vortix AI: Streamlit app with chat, web search, image and PDF understanding,
image creation, downloadable PDFs, saved chats, and 120+ assistant modes.

Run locally:  streamlit run app.py
Deploy free:  Streamlit Community Cloud, from a GitHub repo (see README.md)
"""

import base64
import hmac
import json
import os
import re
import urllib.request
from pathlib import Path

import anthropic
import streamlit as st
from fpdf import FPDF
from openai import OpenAI

from prompts import MASTER_PROMPT, MODES

st.set_page_config(page_title="Vortix AI", page_icon="⚡", layout="centered")

APP_NAME = "Vortix AI"
CLAUDE_MODELS = ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"]
IMAGE_MODEL = "gpt-image-1"  # change if your OpenAI account has a newer image model
FEATURES = ["Chat", "Web search", "Create image", "Create PDF"]
ANSWER_STYLES = {
    "Normal": "",
    "Short": "Keep the answer short: a few sentences or a few bullet points.",
    "Detailed": "Give a detailed, well-organised answer with examples.",
    "Step by step": "Explain step by step, using numbered steps.",
}
FILE_MIME = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}
FONT_FILES = [  # optional Unicode font for Hindi text in PDFs, placed in a "fonts" folder
    "fonts/NotoSansDevanagari-Regular.ttf",
    "fonts/NotoSans-Regular.ttf",
    "fonts/DejaVuSans.ttf",
]
MAX_TOKENS = 4096
MEMORY_WINDOW = 40  # recent messages sent with each request
MAX_UPLOAD_MB = 10
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}
SEARCH_HINT = "\nYou can search the web. Use it for anything recent or uncertain, and name your sources."

# Requests such as "create an image of a fox", "image banao neon city", "sunset ki photo banao".
IMAGE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^\s*(?:please\s+)?(?:create|generate|make|draw)\s+(?:me\s+)?(?:an?\s+|the\s+)?"
        r"(?:image|picture|photo|illustration)s?\s+(?:of|showing|for)\s+(.+)$",
        r"^\s*(?:image|photo|tasveer|picture)\s+(?:bana\s*do|banao|bnao|bana\s*de|banaiye)\s*[:\-]?\s*(.+)$",
        r"^\s*(.+?)\s+(?:ki|ka|ke|wali|wala)\s+(?:image|photo|tasveer|picture)\s+"
        r"(?:bana\s*do|banao|bnao|bana\s*de|banaiye)\b\s*(.*)$",
    )
]
PDF_PATTERN = re.compile(
    r"\bpdf\b.*\b(?:banao|bana\s*do|bana\s*de|bnao|create|make|generate|export)\b"
    r"|\b(?:banao|bana\s*do|bana\s*de|bnao|create|make|generate)\b.*\bpdf\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Settings and access
# ---------------------------------------------------------------------------

def get_secret(name, default=None):
    """Read a setting from Streamlit Secrets first, then from environment variables."""
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass  # no secrets file on this machine
    return os.getenv(name, default)


def require_password():
    """If APP_PASSWORD is set, stop the app until the visitor types it."""
    expected = get_secret("APP_PASSWORD")
    if not expected or st.session_state.get("unlocked"):
        return
    typed = st.text_input("Password", type="password")
    if typed:
        if hmac.compare_digest(typed, str(expected)):
            st.session_state.unlocked = True
            st.rerun()
        st.error("Wrong password.")
    st.stop()


def make_client():
    key = get_secret("ANTHROPIC_API_KEY")
    return anthropic.Anthropic(api_key=key) if key else None


def build_system_prompt(mode, style):
    # SYSTEM_PROMPT in Secrets (your private master prompt) replaces the default one.
    parts = [get_secret("SYSTEM_PROMPT") or MASTER_PROMPT, f"Active mode: {mode}. {MODES[mode]}"]
    if ANSWER_STYLES.get(style):
        parts.append(ANSWER_STYLES[style])
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Claude helpers
# ---------------------------------------------------------------------------

def attachment_block(uploaded):
    ext = uploaded.name.rsplit(".", 1)[-1].lower()
    mime = FILE_MIME[ext]
    data = base64.b64encode(uploaded.getvalue()).decode("ascii")
    kind = "document" if mime == "application/pdf" else "image"
    return {"type": kind, "source": {"type": "base64", "media_type": mime, "data": data}}


def api_messages(text, uploaded=None):
    """Saved chat (text only) plus the new message, with the attached file if there is one."""
    history = st.session_state.messages[-MEMORY_WINDOW:]
    while history and history[0]["role"] != "user":  # a conversation must open with a user turn
        history = history[1:]
    messages = [{"role": m["role"], "content": m["content"]} for m in history]
    content = []
    if uploaded is not None:
        content.append(attachment_block(uploaded))
    content.append({"type": "text", "text": text})
    messages.append({"role": "user", "content": content})
    return messages


def claude_text(client, model, system, messages, tools=None):
    """Non-streaming call. Resumes long web searches that pause mid-turn."""
    while True:
        extra = {"tools": tools} if tools else {}
        response = client.messages.create(
            model=model, max_tokens=MAX_TOKENS, system=system, messages=messages, **extra
        )
        if response.stop_reason != "pause_turn":
            break
        messages = messages + [{"role": "assistant", "content": response.content}]
    text = "".join(block.text for block in response.content if block.type == "text")
    return text, response


def extract_sources(response):
    """Unique (title, url) pairs from the web search results in a response."""
    sources, seen = [], set()
    for block in response.content:
        if block.type != "web_search_tool_result" or not isinstance(block.content, list):
            continue
        for item in block.content:
            url = getattr(item, "url", None)
            if url and url not in seen:
                seen.add(url)
                sources.append((getattr(item, "title", None) or url, url))
    return sources


def sources_markdown(sources):
    if not sources:
        return ""
    lines = []
    for title, url in sources:
        safe_title = title.replace("[", "(").replace("]", ")")
        lines.append(f"- [{safe_title}]({url})")
    return "\n\n**Sources**\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------

def image_subject(text):
    """Image description if the message asks for an image, otherwise None."""
    for pattern in IMAGE_PATTERNS:
        match = pattern.match(text)
        if match:
            return " ".join(part.strip() for part in match.groups() if part and part.strip())
    return None


def render_message(msg, key):
    st.markdown(msg["content"])
    if msg.get("image"):
        st.image(msg["image"])
        st.download_button("Download image", data=msg["image"], file_name="vortix_image.png",
                           mime="image/png", key=f"img_{key}")
    if msg.get("pdf"):
        st.download_button("Download PDF", data=msg["pdf"], file_name=msg.get("pdf_name", "vortix.pdf"),
                           mime="application/pdf", key=f"pdf_{key}")


def run_chat(client, model, system, text, uploaded):
    """Normal chat: tokens appear as they are generated."""
    with client.messages.stream(
        model=model, max_tokens=MAX_TOKENS, system=system, messages=api_messages(text, uploaded)
    ) as stream:
        answer = st.write_stream(stream.text_stream)
    if not answer.strip():
        st.warning("Model ne khaali reply diya. Dobara try karo.")
        return None
    return {"role": "assistant", "content": answer, "image": None}


def run_search(client, model, system, text, uploaded):
    with st.spinner("Searching the web..."):
        answer, response = claude_text(
            client, model, system + SEARCH_HINT, api_messages(text, uploaded), tools=[WEB_SEARCH_TOOL]
        )
    reply = {"role": "assistant", "content": answer + sources_markdown(extract_sources(response)),
             "image": None}
    render_message(reply, key="new")
    return reply


def run_image(subject):
    key = get_secret("OPENAI_API_KEY")
    if not key:
        st.warning("Image creation ke liye OPENAI_API_KEY Secrets me add karo.")
        return None
    with st.spinner("Creating image..."):
        result = OpenAI(api_key=key).images.generate(model=IMAGE_MODEL, prompt=subject, size="1024x1024")
    item = result.data[0]
    if getattr(item, "b64_json", None):
        image_bytes = base64.b64decode(item.b64_json)
    elif getattr(item, "url", None):
        with urllib.request.urlopen(item.url) as resp:
            image_bytes = resp.read()
    else:
        raise ValueError("No image data came back.")
    reply = {"role": "assistant", "content": f"Image created for: {subject}", "image": image_bytes}
    render_message(reply, key="new")
    return reply


def run_pdf_doc(client, model, system, text, uploaded):
    request = (
        f"Write a complete, well-organised document in Markdown for this request:\n{text}\n\n"
        "Use a title (#), headings (##) and bullet points where useful. Output only the document."
    )
    with st.spinner("Writing document..."):
        document, _ = claude_text(client, model, system, api_messages(request, uploaded))
    title = " ".join(text.split())[:80] or "Document"
    reply = {"role": "assistant", "content": document, "pdf": build_pdf(title, document),
             "pdf_name": "vortix_document.pdf"}
    render_message(reply, key="new")
    return reply


def handle_prompt(client, model, feature, mode, style, text, uploaded):
    stored_text = text + (f"\n\n[Attached: {uploaded.name}]" if uploaded is not None else "")
    with st.chat_message("user"):
        st.markdown(stored_text)

    system = build_system_prompt(mode, style)
    subject = image_subject(text) if feature == "Chat" else None
    wants_pdf = feature == "Create PDF" or (feature == "Chat" and bool(PDF_PATTERN.search(text)))
    reply = None
    with st.chat_message("assistant"):
        try:
            if feature == "Create image" or subject:
                reply = run_image(subject or text)
            elif wants_pdf:
                reply = run_pdf_doc(client, model, system, text, uploaded)
            elif feature == "Web search":
                reply = run_search(client, model, system, text, uploaded)
            else:
                reply = run_chat(client, model, system, text, uploaded)
        except anthropic.APIError as err:
            st.error(f"API error: {err}")
        except Exception as err:  # show problems on screen instead of crashing the app
            st.error(f"Error: {err}")

    if reply:
        st.session_state.messages.append({"role": "user", "content": stored_text, "image": None})
        st.session_state.messages.append(reply)


# ---------------------------------------------------------------------------
# PDF tools
# ---------------------------------------------------------------------------

def pdf_font_path():
    base = Path(__file__).resolve().parent
    for name in FONT_FILES:
        if (base / name).exists():
            return str(base / name)
    return None


def build_pdf(title, body):
    font = pdf_font_path()
    family = "Unicode" if font else "Helvetica"
    bullet = "• " if font else "- "

    def clean(text):  # built-in PDF fonts only handle Latin characters
        return text if font else text.encode("latin-1", "replace").decode("latin-1")

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    if font:
        pdf.add_font(family, "", font)
    pdf.set_font(family, size=16)
    pdf.multi_cell(0, 9, clean(title))
    pdf.ln(3)
    for raw in body.splitlines():
        line = raw.rstrip()
        if not line.strip():
            pdf.ln(3)
            continue
        size = 11
        if line.startswith("### "):
            size, line = 12, line[4:]
        elif line.startswith("## "):
            size, line = 13, line[3:]
        elif line.startswith("# "):
            size, line = 15, line[2:]
        line = re.sub(r"^\s*[-*]\s+", bullet, line)
        line = re.sub(r"\*\*(.+?)\*\*|`(.+?)`", lambda m: m.group(1) or m.group(2), line)
        line = re.sub(r"(\S{60})(?=\S)", r"\1 ", line)  # break very long words
        pdf.set_font(family, size=size)
        pdf.multi_cell(0, 6, clean(line))
    return bytes(pdf.output())


# ---------------------------------------------------------------------------
# Sidebar and page
# ---------------------------------------------------------------------------

def plain_messages():
    return [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages]


def valid_chat(data):
    return isinstance(data, list) and all(
        isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)
        for m in data
    )


def sidebar_controls(client):
    with st.sidebar:
        st.header(APP_NAME)
        feature = st.selectbox("Feature", FEATURES)
        mode = st.selectbox("Assistant mode (120+)", list(MODES))
        style = st.selectbox("Answer style", list(ANSWER_STYLES))
        choice = st.selectbox("Claude model", CLAUDE_MODELS + ["Custom"])
        model = choice
        if choice == "Custom":
            model = st.text_input("Custom model name", value=CLAUDE_MODELS[0]).strip()

        if st.button("Test API connection"):
            try:
                test = client.messages.create(
                    model=model, max_tokens=20,
                    messages=[{"role": "user", "content": "Reply with the word OK."}],
                )
                st.success(f"Connected: {test.content[0].text.strip()}")
            except anthropic.APIError as err:
                st.error(f"Connection failed: {err}")

        st.divider()
        if st.button("New chat"):
            st.session_state.messages = []
            st.session_state.loaded_name = None

        if st.session_state.messages:
            transcript = "\n\n".join(
                f"{'You' if m['role'] == 'user' else APP_NAME}: {m['content']}"
                for m in st.session_state.messages
            )
            st.download_button("Download chat as PDF", data=build_pdf(f"{APP_NAME} chat", transcript),
                               file_name="vortix_chat.pdf", mime="application/pdf", key="export_pdf")
            st.download_button("Save chat (JSON)", data=json.dumps(plain_messages(), ensure_ascii=False, indent=2),
                               file_name="vortix_chat.json", mime="application/json", key="export_json")

        loaded = st.file_uploader("Load saved chat (JSON)", type=["json"], key="chat_loader")
        if loaded is not None and st.session_state.get("loaded_name") != loaded.name:
            try:
                data = json.loads(loaded.getvalue().decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                data = None
            if valid_chat(data):
                st.session_state.messages = data
                st.session_state.loaded_name = loaded.name
                st.success("Chat load ho gayi.")
            else:
                st.error("Ye file saved chat JSON jaisi nahi lagti.")
    return feature, mode, style, model


def attachment_panel():
    with st.expander("📎 Attach image or PDF (optional)"):
        uploaded = st.file_uploader("Image ya PDF chuno", type=list(FILE_MIME), key="attachment")
        if uploaded is not None:
            st.caption(f"Attached: {uploaded.name}. Jab tak ye file yahan hai, har message ke saath jaayegi.")
    if uploaded is not None and uploaded.size > MAX_UPLOAD_MB * 1024 * 1024:
        st.error(f"File {MAX_UPLOAD_MB} MB se badi hai.")
        return None
    return uploaded


def main():
    require_password()

    client = make_client()
    if client is None:
        st.error("ANTHROPIC_API_KEY nahi mili. Manage app → ⋮ → Settings → Secrets me key add karo.")
        st.stop()

    if "messages" not in st.session_state:
        st.session_state.messages = []

    feature, mode, style, model = sidebar_controls(client)
    st.title(f"⚡ {APP_NAME}")
    uploaded = attachment_panel()

    for i, msg in enumerate(st.session_state.messages):
        with st.chat_message(msg["role"]):
            render_message(msg, key=f"hist_{i}")

    prompt = st.chat_input("Ask Vortix anything, or say 'create an image of ...'")
    if prompt:
        handle_prompt(client, model, feature, mode, style, prompt.strip(), uploaded)


main()
