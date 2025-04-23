# Cursor Ollama OpenAI API Proxy

A Flask-based proxy that enables Cursor to use locally hosted Ollama models by translating OpenAI API calls to Ollama API calls, with real-time visualization of all traffic between Cursor and your local models.

![Ollama OpenAI API Proxy Logs Dashboard](https://punnerud.github.io/cursor_ollama_proxy/example.png)

## Features

- OpenAI API compatibility layer for Ollama
- Real-time web-based log viewer
- Streaming response support
- Docker support
- Colored console output
- Response timing information

## Prerequisites

1. Ollama installed and running locally on port 11434
2. Docker installed

## Quick Start with Docker

1. Clone this repository
2. Make sure your local Ollama instance is running
3. Run the proxy:
   ```bash
   docker-compose up -d
   ```

The following ports will be exposed:
- `7005`: Proxy server (OpenAI API compatible endpoint + web logs)

The proxy will automatically connect to your local Ollama instance running on port 11434.

4. Expose the proxy using ngrok or similar:
   ```bash
   ngrok http 7005
   ```

## Using with Cursor

1. Copy the ngrok URL (e.g., `https://357c-171-123-237-18.ngrok-free.app/v1`) and paste it into Cursor's OpenAI API settings:
   - Open Preferences
   - Go to Cursor Settings
   - Under Models, paste the URL into the OpenAI API Key setting

2. Add your Ollama model name (e.g., "gemma3:12b-it-qat") to the models list in Cursor settings

3. Select the model in the chat window after activating

## Web Log Viewer

Access the real-time log viewer at:
```
http://localhost:7005/logs
```

## API Usage

The proxy supports OpenAI API-compatible endpoints. Use it as you would use the OpenAI API, but with your local URL:

```python
import openai

# Point to your proxy
openai.api_base = "http://localhost:7005/v1"
# Any string will work as the API key
openai.api_key = "not-needed"

# Make API calls as usual
response = openai.ChatCompletion.create(
    model="gemma3:12b-it-qat",  # or any other Ollama model you have installed
    messages=[
        {"role": "user", "content": "Hello, how are you?"}
    ],
    stream=True  # streaming is supported!
)
```

## Development Setup

If you want to run without Docker:

1. Make sure Ollama is running locally on port 11434

2. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Run the proxy:
   ```bash
   python app.py
   ```

## Environment Variables

- `OLLAMA_BASE_URL`: URL of your Ollama instance (default: http://localhost:11434)

## Docker Build

To build and run just the proxy:

```bash
docker build -t ollama-proxy .
docker run -d -p 7005:7005 \
  --add-host=host.docker.internal:host-gateway \
  -e OLLAMA_BASE_URL=http://host.docker.internal:11434 \
  ollama-proxy
```

## Endpoints

- `/v1/chat/completions`: OpenAI-compatible chat completions
- `/chat/completions`: Alternative endpoint without v1 prefix
- `/logs`: Web-based log viewer
- `/v1/models`: List available models

## License

MIT 