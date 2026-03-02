"""
codex-proxy: OpenAI Chat Completions API → codex exec CLI bridge
Lets OpenClaw use gpt-5.3-codex via ChatGPT Plus OAuth (free).

Isolation: detects which agent is calling from system message content,
then locks codex exec's cwd to that agent's workspace directory.

Streaming: supports both stream=false (JSON) and stream=true (SSE) modes.
"""
import json
import time
import asyncio
import logging
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("codex-proxy")

app = FastAPI()

# Noise lines from codex CLI to strip from output
NOISE = [
    "could not update PATH",
    "failed to write models cache",
    "failed to renew cache TTL",
    "Reconnecting...",
    "Falling back from WebSockets",
    "stream disconnected",
    "websocket closed",
]

# Agent workspace mapping: keywords in system message → workspace path
AGENT_WORKSPACES = [
    (["email_ops.py", "yahoo mail", "imap", "smtp", "stevenwuziyuan"], "/home/steven/claw-email"),
    (["美股", "stock analyst", "股市", "etf", "earnings", "pe ratio"],  "/home/steven/claw-stock"),
]
DEFAULT_WORKSPACE = "/home/steven/claw"


def detect_workspace(messages: list[dict]) -> str:
    """
    Detect agent workspace from system message content.
    OpenClaw includes AGENTS.md content in the system message,
    so each agent has distinctive keywords we can match on.
    """
    for m in messages:
        if m.get("role") == "system":
            content = (m.get("content") or "").lower()
            for keywords, workspace in AGENT_WORKSPACES:
                if any(kw in content for kw in keywords):
                    return workspace
    return DEFAULT_WORKSPACE


def build_prompt(messages: list[dict]) -> str:
    """Convert OpenClaw's multi-turn messages into a single codex prompt."""
    parts = []
    for m in messages:
        role = m.get("role", "")
        content = m.get("content") or ""
        if isinstance(content, list):
            # Handle multimodal content blocks (OpenClaw sends content as array)
            content = " ".join(
                block.get("text", "") for block in content if isinstance(block, dict)
            )
        if role == "system":
            parts.append(f"[Instructions]\n{content}")
        elif role == "user":
            parts.append(f"[User]\n{content}")
        elif role == "assistant":
            # Previous assistant turns may have empty content (content:[]) — skip them
            if content:
                parts.append(f"[Assistant]\n{content}")
    return "\n\n".join(parts)


async def run_codex(prompt: str, workspace: str) -> str:
    """Run codex exec and return cleaned output text."""
    proc = await asyncio.create_subprocess_exec(
        "/home/steven/.npm-global/bin/codex", "exec",
        "--model", "gpt-5.3-codex",
        "-s", "danger-full-access",
        "--skip-git-repo-check",
        "--dangerously-bypass-approvals-and-sandbox",
        "--ephemeral",
        prompt,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=workspace,
    )

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
    except asyncio.TimeoutError:
        log.warning("codex exec timed out after 600s")
        return "Timed out after 600s"

    result = stdout.decode().strip()
    result = "\n".join(
        line for line in result.splitlines()
        if not any(noise in line for noise in NOISE)
    )

    if not result:
        err = stderr.decode().strip()[:300]
        result = f"No output from codex.{' stderr: ' + err if err else ''}"

    return result


@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def chat(req: Request):
    body = await req.json()
    messages = body.get("messages", [])
    stream = body.get("stream", False)
    prompt = build_prompt(messages)
    workspace = detect_workspace(messages)

    log.info(f"→ codex exec | workspace: {workspace} | stream={stream} | prompt: {len(prompt)} chars")

    result = await run_codex(prompt, workspace)

    log.info(f"← codex result | workspace: {workspace} | {len(result)} chars | first50={repr(result[:50])}")

    if stream:
        return _make_streaming_response(result)
    else:
        return _make_response(result)


def _make_response(content: str) -> dict:
    """Non-streaming JSON response (OpenAI chat completions format)."""
    return {
        "id": "codex-proxy",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "gpt-5.3-codex",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _make_streaming_response(content: str) -> StreamingResponse:
    """SSE streaming response (OpenAI chat completions stream format)."""
    run_id = f"codex-proxy-{int(time.time())}"

    def generate():
        # First chunk: role
        role_chunk = {
            "id": run_id, "object": "chat.completion.chunk",
            "created": int(time.time()), "model": "gpt-5.3-codex",
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(role_chunk)}\n\n"

        # Content chunk: full text in one shot
        content_chunk = {
            "id": run_id, "object": "chat.completion.chunk",
            "created": int(time.time()), "model": "gpt-5.3-codex",
            "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(content_chunk)}\n\n"

        # Stop chunk
        stop_chunk = {
            "id": run_id, "object": "chat.completion.chunk",
            "created": int(time.time()), "model": "gpt-5.3-codex",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        yield f"data: {json.dumps(stop_chunk)}\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/v1/models")
async def models():
    """OpenClaw may call this to list available models."""
    return {
        "object": "list",
        "data": [{"id": "gpt-5.3-codex", "object": "model", "owned_by": "codex-proxy"}],
    }


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=9999, log_level="info")
