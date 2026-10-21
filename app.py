"""
Vortix AI: Streamlit app with chat, web search, image and PDF understanding,
image creation, downloadable PDFs, saved chats, and 120+ assistant modes.

The default AI is Google Gemini (free tier). Claude is optional if you add credit.

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
from google import genai
from google.genai import types
from openai import OpenAI

from prompts import MASTER_PROMPT, MODES

st.set_page_config(page_title="Vortix AI", page_icon="⚡", layout="centered")

APP_NAME = "Vortix AI"
GEMINI = "Gemini (free)"
CLAUDE = "Claude (paid)"
PROVIDERS = [GEMINI, CLAUDE]
MODELS = {  # first entry is the default; "Custom" lets you type any model ID
    GEMINI: ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro"],
    CLAUDE: ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"],
}
MISSING_KEY = {GEMINI: "GEMINI_API_KEY", CLAUDE: "ANTHROPIC_API_KEY"}
IMAGE_MODEL = "gpt-image-1"  # OpenAI image model (paid). Change if you have a newer one.
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
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}  # Claude only
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
# Settings, access, and clients
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


def make_client(provider):
    if provider == GEMINI:
        key = get_secret("GEMINI_API_KEY")
        return genai.Client(api_key=key) if key else None
    key = get_secret("ANTHROPIC_API_KEY")
    return anthropic.Anthropic(api_key=key) if key else None


def build_system_prompt(mode, style):
    # SYSTEM_PROMPT in Secrets (your private master prompt) replaces the default one.
    parts = [get_secret("SYSTEM_PROMPT") or MASTER_PROMPT, f"Active mode: {mode}. {MODES[mode]}"]
    if ANSWER_STYLES.get(style):
        parts.append(ANSWER_STYLES[style])
    return "\n\n".join(parts)


def friendly_error(err):
    text = str(err)
    if "429" in text or "RESOURCE_EXHAUSTED" in text or "quota" in text.lower():
        return "Free limit abhi khatam hai. Thodi der baad try karo, ya sidebar me dusra model chuno."
    if "404" in text or "NOT_FOUND" in text:
        return "Model ID nahi mila. Sidebar me 'Custom' chuno aur sahi model ID daalo."
    if any(code in text for code in ("401", "403", "API_KEY", "invalid x-api-key", "PERMISSION_DENIED")):
        return "API key galat ya band hai. Secrets me key check karo."
    return text


def test_connection(provider, client, model):
    try:
        if provider == GEMINI:
            reply = client.models.generate_content(model=model, contents="Reply with the word OK.")
            return True, (reply.text or "").strip()
        reply = client.messages.create(
            model=model, max_tokens=20, messages=[{"role": "user", "content": "Reply with the word OK."}]
        )
        return True, reply.content[0].text.strip()
    except Exception as err:
        return False, friendly_error(err)


# ---------------------------------------------------------------------------
# Chat helpers for both providers
# ---------------------------------------------------------------------------

def recent_turns():
    """Last saved messages, starting with a user turn."""
    turns = st.session_state.messages[-MEMORY_WINDOW:]
    while turns and turns[0]["role"] != "user":  # a conversation must open with a user turn
        turns = turns[1:]
    return turns


def file_mime(uploaded):
    return FILE_MIME[uploaded.name.rsplit(".", 1)[-1].lower()]


def gemini_contents(text, uploaded=None):
    contents = [
        types.Content(
            role="model" if m["role"] == "assistant" else "user",
            parts=[types.Part(text=m["content"])],
        )
        for m in recent_turns()
    ]
    parts = []
    if uploaded is not None:
        parts.append(types.Part.from_bytes(data=uploaded.getvalue(), mime_type=file_mime(uploaded)))
    parts.append(types.Part(text=text))
    contents.append(types.Content(role="user", parts=parts))
    return contents


def gemini_config(system, search=False):
    return types.GenerateContentConfig(
        system_instruction=system,
        max_output_tokens=MAX_TOKENS,
        tools=[types.Tool(google_search=types.GoogleSearch())] if search else None,
    )


def gemini_sources(response):
    sources, seen = [], set()
    candidates = response.candidates or []
    meta = getattr(candidates[0], "grounding_metadata", None) if candidates else None
    for chunk in getattr(meta, "grounding_chunks", None) or []:
        web = getattr(chunk, "web", None)
        uri = getattr(web, "uri", None)
        if uri and uri not in seen:
            seen.add(uri)
            sources.append((getattr(web, "title", None) or uri, uri))
    return sources


def claude_messages(text, uploaded=None):
    messages = [{"role": m["role"], "content": m["content"]} for m in recent_turns()]
    content = []
    if uploaded is not None:
        mime = file_mime(uploaded)
        data = base64.b64encode(uploaded.getvalue()).decode("ascii")
        kind = "document" if mime == "application/pdf" else "image"
        content.append({"type": kind, "source": {"type": "base64", "media_type": mime, "data": data}})
    content.append({"type": "text", "text": text})
    messages.append({"role": "user", "content": content})
    return messages


def claude_sources(response):
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


def stream_text(provider, client, model, system, text, uploaded=None):
    """Yields reply text as it arrives, for either provider."""
    if provider == GEMINI:
        for chunk in client.models.generate_content_stream(
            model=model, contents=gemini_contents(text, uploaded), config=gemini_config(system)
        ):
            if chunk.text:
                yield chunk.text
    else:
        with client.messages.stream(
            model=model, max_tokens=MAX_TOKENS, system=system, messages=claude_messages(text, uploaded)
        ) as stream:
            yield from stream.text_stream


def complete_text(provider, client, model, system, text, uploaded=None, search=False):
    """One non-streaming reply. Returns (text, sources), where sources are (title, url) pairs."""
    if provider == GEMINI:
        response = client.models.generate_content(
            model=model, contents=gemini_contents(text, uploaded), config=gemini_config(system, search)
        )
        return response.text or "", gemini_sources(response)

    extra = {"tools": [WEB_SEARCH_TOOL]} if search else {}
    messages = claude_messages(text, uploaded)
    while True:  # long web searches can pause mid-turn; resume them
        response = client.messages.create(
            model=model, max_tokens=MAX_TOKENS, system=system, messages=messages, **extra
        )
        if response.stop_reason != "pause_turn":
            break
        messages = messages + [{"role": "assistant", "content": response.content}]
    answer = "".join(block.text for block in response.content if block.type == "text")
    return answer, claude_sources(response)


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


def run_chat(settings, text, uploaded):
    system = build_system_prompt(settings["mode"], settings["style"])
    answer = st.write_stream(
        stream_text(settings["provider"], settings["client"], settings["model"], system, text, uploaded)
    )
    if not answer.strip():
        st.warning("Model ne khaali reply diya. Dobara try karo.")
        return None
    return {"role": "assistant", "content": answer, "image": None}


def run_search(settings, text, uploaded):
    system = build_system_prompt(settings["mode"], settings["style"]) + SEARCH_HINT
    with st.spinner("Searching the web..."):
        answer, sources = complete_text(
            settings["provider"], settings["client"], settings["model"], system, text, uploaded, search=True
        )
    reply = {"role": "assistant", "content": answer + sources_markdown(sources), "image": None}
    render_message(reply, key="new")
    return reply


def run_pdf_doc(settings, text, uploaded):
    request = (
        f"Write a complete, well-organised document in Markdown for this request:\n{text}\n\n"
        "Use a title (#), headings (##) and bullet points where useful. Output only the document."
    )
    system = build_system_prompt(settings["mode"], settings["style"])
    with st.spinner("Writing document..."):
        document, _ = complete_text(
            settings["provider"], settings["client"], settings["model"], system, request, uploaded
        )
    title = " ".join(text.split())[:80] or "Document"
    reply = {"role": "assistant", "content": document, "pdf": build_pdf(title, document),
             "pdf_name": "vortix_document.pdf"}
    render_message(reply, key="new")
    return reply


def run_image(subject):
    key = get_secret("OPENAI_API_KEY")
    if not key:
        st.warning("Image creation ke liye OPENAI_API_KEY Secrets me add karo (ye paid hai).")
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


def handle_prompt(settings, text, uploaded):
    stored_text = text + (f"\n\n[Attached: {uploaded.name}]" if uploaded is not None else "")
    with st.chat_message("user"):
        st.markdown(stored_text)

    feature = settings["feature"]
    subject = image_subject(text) if feature == "Chat" else None
    wants_pdf = feature == "Create PDF" or (feature == "Chat" and bool(PDF_PATTERN.search(text)))
    reply = None
    with st.chat_message("assistant"):
        try:
            if feature == "Create image" or subject:
                reply = run_image(subject or text)
            elif wants_pdf:
                reply = run_pdf_doc(settings, text, uploaded)
            elif feature == "Web search":
                reply = run_search(settings, text, uploaded)
            else:
                reply = run_chat(settings, text, uploaded)
        except Exception as err:  # show problems on screen instead of crashing the app
            hint = friendly_error(err)
            st.error(hint if hint == str(err) else f"{hint}\n\nDetail: {err}")

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


def sidebar_controls():
    with st.sidebar:
        st.header(APP_NAME)
        feature = st.selectbox("Feature", FEATURES)
        provider = st.selectbox("AI provider", PROVIDERS)
        choice = st.selectbox("Model", MODELS[provider] + ["Custom"])
        model = choice
        if choice == "Custom":
            model = st.text_input("Custom model ID", value=MODELS[provider][0]).strip()
        mode = st.selectbox("Assistant mode (120+)", list(MODES))
        style = st.selectbox("Answer style", list(ANSWER_STYLES))
        client = make_client(provider)

        if st.button("Test API connection"):
            if client is None:
                st.error(f"{MISSING_KEY[provider]} Secrets me nahi mili.")
            else:
                ok, info = test_connection(provider, client, model)
                if ok:
                    st.success(f"Connected: {info}")
                else:
                    st.error(info)

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
            st.download_button("Save chat (JSON)",
                               data=json.dumps(plain_messages(), ensure_ascii=False, indent=2),
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

    return {"feature": feature, "provider": provider, "model": model,
            "mode": mode, "style": style, "client": client}


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
    if "messages" not in st.session_state:
        st.session_state.messages = []

    settings = sidebar_controls()
    if settings["client"] is None:
        st.error(f"{MISSING_KEY[settings['provider']]} nahi mili. Manage app → ⋮ → Settings → Secrets me key add karo.")
        st.stop()

    st.title(f"⚡ {APP_NAME}")
    uploaded = attachment_panel()

    for i, msg in enumerate(st.session_state.messages):
        with st.chat_message(msg["role"]):
            render_message(msg, key=f"hist_{i}")

    prompt = st.chat_input("Ask Vortix anything, or say 'create an image of ...'")
    if prompt:
        handle_prompt(settings, prompt.strip(), uploaded)


main()
