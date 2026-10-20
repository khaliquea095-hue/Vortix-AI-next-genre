# Vortix AI

A Streamlit chat app powered by Claude.

## Features

- Chat with streaming replies, pick a Claude model or type any model name
- 120+ assistant modes in `prompts.py`, plus answer styles
- Web search with sources
- Attach an image or PDF and ask questions about it
- Create images (OpenAI)
- Create downloadable PDF documents, and download the whole chat as PDF
- Save and load chats (JSON)
- Optional password lock and a "Test API connection" button

## Files

- `app.py`: the app
- `prompts.py`: master prompt and modes (edit freely)
- `requirements.txt`: Python packages
- `.gitignore`: keeps secret files out of GitHub

## Secrets

On Streamlit Community Cloud, add these in Manage app → Settings → Secrets. Locally, put them in `.streamlit/secrets.toml`.

```toml
ANTHROPIC_API_KEY = "sk-ant-..."
OPENAI_API_KEY = "sk-..."                   # optional, for image creation
APP_PASSWORD = "choose-a-strong-password"   # optional, recommended
SYSTEM_PROMPT = """Your private master prompt"""  # optional
```

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy on Streamlit Community Cloud

1. Push these files to a GitHub repository.
2. Go to share.streamlit.io, create a new app, and select the repository, branch `main`, and main file `app.py`.
3. Add the secrets above, then deploy. Later commits to the repository update the app.

## Hindi text in PDFs (optional)

Built-in PDF fonts support only Latin characters. For Hindi, download `NotoSansDevanagari-Regular.ttf` from Google Fonts and place it in a `fonts/` folder next to `app.py`.

## Notes

- Keep API keys only in Secrets. Never paste them into code.
- API usage is billed to the key owner. Set spend limits in the provider consoles and keep `APP_PASSWORD` set.
- Chat history resets on refresh. Use "Save chat (JSON)" and "Load saved chat" to keep conversations.
- Not included: voice input, Word, Excel and PowerPoint generation, and permanent cloud memory, which needs a database.
