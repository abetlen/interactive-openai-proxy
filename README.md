# Interactive OpenAI Proxy

python app to intercept requests / responses to OpenAI compatible API's and modify them through a simple web interface before returning them to the client.

Useful for debugging and testing.

# Usage

```bash
pip install -r requirements.txt
# optional set OPENAI_BASE_URL to the server you want to proxy requests to
export OPENAI_BASE_URL=https://api.openai.com
uvicorn app:app --reload
```

Requests that are sent to the proxy will be displayed in the web interface at http://localhost:8000 and can be modified manually before being returned to the client.

# License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.
