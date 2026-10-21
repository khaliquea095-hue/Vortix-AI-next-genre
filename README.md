# Vortix AI

A Streamlit chat app. The default AI is Google Gemini, which has a free tier. Claude is an optional paid choice.

## Features

- Chat with streaming replies. Pick Gemini (free) or Claude (paid), or type any model ID
- 120+ assistant modes in `prompts.py`, plus answer styles
- Web search with sources
- Attach an image or PDF and ask questions about it
- Create images (OpenAI, paid)
- Create downloadable PDF documents, and download the whole chat as PDF
- Save and load chats (JSON)
- Optional password lock and a "Test API connection" button

## Files

- `app.py`: the app
- `prompts.py`: master prompt and modes (edit freely)
- `requirements.txt`: Python packages
- `.gitignore`: keeps secret files out of GitHub

## Get a free Gemini key

1. Open Google AI Studio and sign in with a Google account.
2. Click **Get API key**, then **Create API key**.
3. Copy the key and put it in Secrets as shown below. Don't paste it in chat or in code.

## Secrets

On Streamlit Community Cloud, add these in Manage app → Settings → Secrets. Locally, put them in `.streamlit/secrets.toml`.

```toml
GEMINI_API_KEY = "your-gemini-key"
APP_PASSWORD = "choose-a-strong-password"   # optional, recommended
OPENAI_API_KEY = "sk-..."                   # optional, only for image creation
ANTHROPIC_API_KEY = "sk-ant-..."            # optional, only if you use Claude
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

- Free Gemini limits are per day and per model. If you see a limit message, wait a while or pick another model.
- On the free tier, Google may use the content you send to improve its models. Don't send private or sensitive information.
- Image creation uses OpenAI, which is paid. Without `OPENAI_API_KEY` the image feature stays off.
- Chat history resets on refresh. Use "Save chat (JSON)" and "Load saved chat" to keep conversations.
- Not included: voice input, Word, Excel and PowerPoint generation, and permanent cloud memory, which needs a database.
