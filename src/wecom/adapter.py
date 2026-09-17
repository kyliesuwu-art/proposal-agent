from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from src.wecom.models import AgentEvent
from src.wecom.task_service import TaskService


@dataclass(frozen=True)
class IncomingMessage:
    userid: str
    chatid: str
    content: str
    message_id: str | None = None


class WeComTransport(Protocol):
    async def reply_text(self, frame: object, text: str) -> None: ...
    async def send_text(self, chatid: str, text: str) -> None: ...
    async def send_file(self, chatid: str, path: Path) -> None: ...


class WeComAgentAdapter:
    """Maps WeCom messages/events to the transport-neutral task service."""

    def __init__(self, service: TaskService, transport: WeComTransport) -> None:
        self._service, self._transport, self._loop = service, transport, None
        service.subscribe(self._on_event)

    async def handle(self, message: IncomingMessage, frame: object) -> None:
        self._loop = asyncio.get_running_loop()
        _, response = self._service.receive(message)
        await self._transport.reply_text(frame, response)

    async def handle_markdown_upload(self, message: IncomingMessage, filename: str, content: bytes, frame: object) -> None:
        self._loop = asyncio.get_running_loop()
        _, response = self._service.approve_uploaded_markdown(
            message.userid,
            message.chatid,
            filename,
            content,
            message_id=message.message_id,
        )
        await self._transport.reply_text(frame, response)

    async def reply(self, frame: object, text: str) -> None:
        await self._transport.reply_text(frame, text)

    def _on_event(self, event: AgentEvent) -> None:
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(lambda: asyncio.create_task(self._deliver_event(event)))

    async def _deliver_event(self, event: AgentEvent) -> None:
        task = event.task
        if event.name == "MD_GENERATION_COMPLETED":
            await self._transport.send_text(task.chatid, f"方案 Markdown 已生成，请先确认内容。任务编号：{task.task_id}")
            if task.proposal_md_path:
                await self._transport.send_file(task.chatid, Path(task.proposal_md_path))
        elif event.name == "MD_GENERATION_FAILED":
            await self._transport.send_text(task.chatid, f"方案生成失败。失败阶段：proposal_generation。任务编号：{task.task_id}")
        elif event.name == "MD_APPROVED":
            await self._transport.send_text(task.chatid, f"Markdown 已确认。任务编号：{task.task_id}")


class SDKTransport:
    """Thin wrapper around the installed WeCom SDK; no business state lives here."""

    def __init__(self, client) -> None:
        self._client = client

    async def reply_text(self, frame: object, text: str) -> None:
        from wecom_aibot_sdk import generate_req_id
        await self._client.reply_stream(frame, generate_req_id("stream"), text, finish=True)

    async def send_text(self, chatid: str, text: str) -> None:
        await self._client.send_message(chatid, {"msgtype": "markdown", "markdown": {"content": text}})

    async def send_file(self, chatid: str, path: Path) -> None:
        await self._client.send_media_message(chatid, str(path))
