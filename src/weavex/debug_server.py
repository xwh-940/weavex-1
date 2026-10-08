"""FastAPI semantic workbench for stepping through the in-memory prototype."""

from __future__ import annotations

import asyncio
import mimetypes
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from openai import AsyncOpenAI

from .demo import ObservatoryDemo
from .local_world import semantic_address
from .protocol import SemanticHandle, handle_data, parse_handles
from .provider import OpenAICompatibleModel, UnavailableModel
from .defaults import build_default_runtime
from .runtime import PrototypeRuntime


def runtime_from_env() -> PrototypeRuntime:
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if api_key:
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        )
        max_tokens = os.getenv("DEEPSEEK_MAX_TOKENS", "").strip()
        model = OpenAICompatibleModel(
            client,
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
            **({"max_tokens": int(max_tokens)} if max_tokens else {}),
        )
    else:
        model = UnavailableModel(
            "DEEPSEEK_API_KEY is not configured; inject and inspect work, "
            "but semantic step/run requires a provider"
        )
    return build_default_runtime(model)


def create_app(runtime: PrototypeRuntime) -> FastAPI:
    app = FastAPI(title="WeaveX Semantic Workbench")
    app.state.runtime = runtime
    app.state.step_lock = asyncio.Lock()
    app.state.demo = ObservatoryDemo()
    debug_path = Path(__file__).with_name("debug")
    index_path = debug_path / "index.html"
    demo_index_path = debug_path / "demo.html"
    mimetypes.add_type("text/javascript", ".js")
    mimetypes.add_type("text/css", ".css")
    app.mount("/debug", StaticFiles(directory=debug_path), name="debug-assets")

    def state_data(*, step_in_flight: bool | None = None) -> dict[str, object]:
        value = runtime.snapshot()
        value["debug_control"] = {
            "step_in_flight": (
                app.state.step_lock.locked()
                if step_in_flight is None
                else step_in_flight
            ),
        }
        value["demo"] = app.state.demo.public_data(runtime)
        return value

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(index_path)

    @app.get("/demo")
    async def demo_index() -> FileResponse:
        return FileResponse(demo_index_path)

    @app.get("/api/state")
    async def state() -> dict[str, object]:
        return state_data()

    @app.get("/api/actor-view")
    async def actor_view(actor_id: str, thread_id: str) -> dict[str, object]:
        try:
            return runtime.actor_view(actor_id, thread_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/inject")
    async def inject(command: dict[str, Any]) -> dict[str, object]:
        async with app.state.step_lock:
            try:
                raw_context = command.get("interpretation_context")
                context = {} if raw_context is None else raw_context
                if not isinstance(context, dict):
                    raise ValueError("interpretation_context must be an object")
                new_context = command.get("new_context", False)
                if not isinstance(new_context, bool):
                    raise ValueError("new_context must be a boolean")
                act = runtime.inject_human(
                    str(command.get("raw_question") or ""),
                    recipient=(
                        None
                        if new_context
                        else str(command.get("recipient") or runtime.entry_broker_id)
                    ),
                    thread_id=(
                        str(command["thread_id"]) if command.get("thread_id") else None
                    ),
                    interpretation_content=context.get("content"),
                    semantic_addresses=parse_handles(
                        context.get("semantic_addresses", [])
                    ),
                    intent=command.get("intent"),
                    new_context=new_context,
                )
                return {
                    "act": runtime.act_data(act),
                    "state": state_data(step_in_flight=False),
                }
            except (KeyError, PermissionError, TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/step")
    async def step() -> dict[str, object]:
        async with app.state.step_lock:
            try:
                handled = await runtime.step()
                return {
                    "handled": runtime.arrival_data(handled) if handled else None,
                    "state": state_data(step_in_flight=False),
                }
            except Exception as exc:
                raise HTTPException(
                    status_code=409,
                    detail=f"{type(exc).__name__}: {exc}",
                ) from exc

    @app.post("/api/human-reply")
    async def human_reply(command: dict[str, Any]) -> dict[str, object]:
        async with app.state.step_lock:
            try:
                if "kind" in command:
                    raise ValueError(
                        "Human incremental inputs do not choose an Act kind"
                    )
                act = runtime.reply_human(
                    thread_id=str(command.get("thread_id") or ""),
                    reply_to=str(command.get("reply_to") or ""),
                    content=str(command.get("content") or ""),
                )
                return {
                    "act": runtime.act_data(act),
                    "state": state_data(step_in_flight=False),
                }
            except (KeyError, PermissionError, TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/run")
    async def run(command: dict[str, Any] | None = None) -> dict[str, object]:
        try:
            limit = 100 if command is None else int(command.get("max_steps") or 100)
            if limit < 1:
                raise ValueError("max_steps must be positive")
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        async with app.state.step_lock:
            try:
                handled = await runtime.run_until_idle(max_steps=limit)
                return {
                    "handled": handled,
                    "state": state_data(step_in_flight=False),
                }
            except Exception as exc:
                raise HTTPException(
                    status_code=409,
                    detail=f"{type(exc).__name__}: {exc}",
                ) from exc

    @app.post("/api/reset")
    async def reset() -> dict[str, object]:
        async with app.state.step_lock:
            runtime.reset()
            app.state.demo.clear()
            return state_data(step_in_flight=False)

    @app.post("/api/demo/load")
    async def load_demo() -> dict[str, object]:
        async with app.state.step_lock:
            try:
                act = app.state.demo.load(runtime)
                return {
                    "act": runtime.act_data(act),
                    "state": state_data(step_in_flight=False),
                }
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/demo/next")
    async def next_demo() -> dict[str, object]:
        async with app.state.step_lock:
            try:
                act = app.state.demo.advance(runtime)
                return {
                    "act": runtime.act_data(act) if act else None,
                    "state": state_data(step_in_flight=False),
                }
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/world-object")
    async def add_world_object(command: dict[str, Any]) -> dict[str, object]:
        async with app.state.step_lock:
            content = str(command.get("content") or "").strip()
            if not content:
                raise HTTPException(status_code=400, detail="content cannot be blank")
            value = runtime.local_worlds.world.add_object(content)
            return {
                "semantic_address": handle_data(
                    SemanticHandle("object", value.object_id)
                ),
                "state": state_data(step_in_flight=False),
            }

    @app.post("/api/world-reference")
    async def add_world_reference(command: dict[str, Any]) -> dict[str, object]:
        async with app.state.step_lock:
            target = command.get("target")
            if not isinstance(target, dict):
                raise HTTPException(status_code=400, detail="target must be an address")
            meaning = command.get("meaning", "")
            if not isinstance(meaning, str):
                raise HTTPException(status_code=400, detail="meaning must be a string")
            try:
                handle = SemanticHandle(str(target.get("kind")), str(target.get("id")))
                value = runtime.local_worlds.world.add_reference(
                    str(command.get("source_object_id") or ""),
                    semantic_address(handle),
                    meaning=meaning.strip(),
                )
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {
                "semantic_address": handle_data(
                    SemanticHandle("reference", value.reference_id)
                ),
                "state": state_data(step_in_flight=False),
            }

    return app


app = create_app(runtime_from_env())


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8766)
