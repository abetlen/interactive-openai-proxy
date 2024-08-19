"""Single file python app that lets users intercept and modify requests to openai compatible api's.
"""
import os
import json
import uuid
import time
import typing
import asyncio
import logging

import httpx
import openai
import uvicorn

from pydantic import BaseModel

from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from starlette.background import BackgroundTask


app = FastAPI()

# Dictionary to store open requests
open_requests = {}

OPENAI_API_BASE = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")
proxy_client = httpx.AsyncClient(base_url=OPENAI_API_BASE)
openai_client = openai.OpenAI(base_url=OPENAI_API_BASE)


class ChatCompletionRequest(BaseModel):
    request: typing.Any
    response: typing.Any = None


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    request_id = str(uuid.uuid4())
    request = await request.json()
    open_requests[request_id] = ChatCompletionRequest(request=request)
    print(f"New request: http://localhost:8000/r/{request_id}")

    # Wait for user modification
    while request_id in open_requests and open_requests[request_id].response is None:
        await asyncio.sleep(1)

    response = open_requests[request_id].response
    del open_requests[request_id]
    return JSONResponse(content=response)


@app.api_route(
    "/v1/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]
)
async def proxy_to_openai(request: Request, path: str):
    headers = {
        key: value for key, value in request.headers.items() if key.lower() != "host"
    }

    # Construct the target URL without the '/v1' prefix
    url = httpx.URL(path=path, query=request.url.query.encode("utf-8"))

    rp_req = proxy_client.build_request(
        request.method, url, headers=headers, content=await request.body()
    )
    rp_resp = await proxy_client.send(rp_req, stream=True)
    return StreamingResponse(
        rp_resp.aiter_raw(),
        status_code=rp_resp.status_code,
        headers=rp_resp.headers,
        background=BackgroundTask(rp_resp.aclose),
    )


@app.get("/")
@app.post("/")
async def home():
    return HTMLResponse(
        content=f"""
    <html>
        <head>
            <title>OpenAI Chat API Request Interceptor</title>
        </head>
        <body>
            <h1>Open Requests</h1>
            <ul>
                {"".join(f'<li><a href="/r/{request_id}">{request_id}</a></li>' for request_id in open_requests if open_requests[request_id].response is None)}
            </ul>
        </body>
    </html>
    """
    )


@app.get("/r/{request_id}", response_class=HTMLResponse)
async def get_request(request_id: str):
    if request_id not in open_requests:
        raise HTTPException(status_code=404, detail="Request not found")

    request = open_requests[request_id]

    kwargs = {}
    if "messages" in request.request:
        kwargs["messages"] = request.request["messages"]
    if "tools" in request.request:
        kwargs["tools"] = request.request["tools"]
    if "tool_choice" in request.request:
        kwargs["tool_choice"] = request.request["tool_choice"]

    content = ""
    tool_name = ""
    tool_arguments = ""
    try:
        response = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            **kwargs,
        )
        if response.choices[0].message.tool_calls:
            tool_call = response.choices[0].message.tool_calls[0]
            tool_name = tool_call.function.name
            tool_arguments = tool_call.function.arguments
        else:
            content = (
                response.choices[0].message.content.strip()
                if response.choices[0].message.content
                else ""
            )
    except Exception as e:
        logging.error(f"An error occurred: {e}")

    html_content = f"""
    <html>
        <head>
            <title>Handle Chat Request</title>
        </head>
        <body>
            <h2>Request:</h2>
            <pre><code>{json.dumps(request.request, indent=2)}</code></pre>
            <h2>Response:</h2>
            <form id="modifyForm" action="/r/{request_id}" method="post">
                <label for="responseType">Response Type:</label>
                <select id="responseType" name="response_type">
                    <option value="content" {"selected" if not tool_name else ""}>Content</option>
                    <option value="tool_call" {"selected" if tool_name else ""}>Tool Call</option>
                </select><br><br>
                <div id="contentSection" style="display: {"block" if not tool_name else "none"}">
                    <label for="responseBody">Content:</label><br>
                    <textarea id="responseBody" name="content" rows="10" cols="50">{content}</textarea><br>
                </div>
                <div id="toolSection" style="display: {"block" if tool_name else "none"}">
                    <label for="toolName">Tool Name:</label><br>
                    <input type="text" id="toolName" name="tool_name" value="{tool_name}"><br>
                    <label for="toolArguments">Tool Arguments (JSON):</label><br>
                    <textarea id="toolArguments" name="tool_arguments" rows="10" cols="50" autofocus>{tool_arguments}</textarea><br>
                </div>
                <button type="submit">Submit</button>
            </form>
            <script>
                document.getElementById('responseType').addEventListener('change', function() {{
                    document.getElementById('contentSection').style.display = this.value === 'content' ? 'block' : 'none';
                    document.getElementById('toolSection').style.display = this.value === 'tool_call' ? 'block' : 'none';
                }});
            </script>
        </body>
    </html>
    """

    return HTMLResponse(content=html_content)


@app.post("/r/{request_id}")
async def modify_request(
    request_id: str,
    response_type: str = Form(...),
    content: str = Form(None),
    tool_name: str = Form(None),
    tool_arguments: str = Form(None),
):
    if request_id not in open_requests:
        raise HTTPException(status_code=404, detail="Request not found")

    request = open_requests[request_id]

    response = {
        "id": f"chatcmpl-{request_id}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": request.request.get("model", "gpt-3.5-turbo"),
        "choices": [{"index": 0, "message": {}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": sum(
                len(m.get("content", "").split())
                for m in request.request.get("messages", [])
            ),
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }

    if response_type == "content":
        response["choices"][0]["message"]["content"] = content
        response["usage"]["completion_tokens"] = len(content.split())
    else:
        response["choices"][0]["message"]["tool_calls"] = [
            {
                "id": f"call_{uuid.uuid4()}",
                "type": "function",
                "function": {"name": tool_name, "arguments": tool_arguments},
            }
        ]
        response["choices"][0]["message"]["content"] = None
        response["usage"]["completion_tokens"] = len(tool_name.split()) + len(
            tool_arguments.split()
        )

    response["usage"]["total_tokens"] = (
        response["usage"]["prompt_tokens"] + response["usage"]["completion_tokens"]
    )

    open_requests[request_id].response = response

    return RedirectResponse(url="/")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
