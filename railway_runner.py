"""Run Shopvpn's bot and web processes inside one Railway service.

All processes intentionally share one service and one mounted volume because
the application uses SQLite. If any required child exits, the runner stops the
others and exits non-zero so Railway can restart the complete deployment.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys


def _enabled(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


async def _shutdown(processes: list[tuple[str, asyncio.subprocess.Process]]) -> None:
    for _, process in processes:
        if process.returncode is None:
            process.terminate()

    try:
        await asyncio.wait_for(
            asyncio.gather(*(process.wait() for _, process in processes)),
            timeout=12,
        )
    except asyncio.TimeoutError:
        for _, process in processes:
            if process.returncode is None:
                process.kill()
        await asyncio.gather(*(process.wait() for _, process in processes))


async def run() -> int:
    port = os.getenv("PORT", "8000")
    commands: list[tuple[str, list[str]]] = [
        ("bot", [sys.executable, "main.py"]),
        (
            "miniapp",
            [
                sys.executable,
                "-m",
                "uvicorn",
                "miniapp.server:app",
                "--host",
                "0.0.0.0",
                "--port",
                port,
                "--proxy-headers",
                "--forwarded-allow-ips",
                "*",
            ],
        ),
    ]

    if _enabled("ENABLE_ADMIN_PANEL"):
        commands.append(
            (
                "admin-panel",
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "admin_panel.server:app",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    os.getenv("ADMIN_PANEL_PORT", "8002"),
                    "--proxy-headers",
                    "--forwarded-allow-ips",
                    "*",
                ],
            )
        )

    processes: list[tuple[str, asyncio.subprocess.Process]] = []
    for name, command in commands:
        print(f"[railway] starting {name}", flush=True)
        process = await asyncio.create_subprocess_exec(*command)
        processes.append((name, process))

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    stop_task = asyncio.create_task(stop_event.wait())
    exit_tasks = {
        asyncio.create_task(process.wait()): name for name, process in processes
    }

    try:
        done, _ = await asyncio.wait(
            [stop_task, *exit_tasks], return_when=asyncio.FIRST_COMPLETED
        )
        if stop_task in done:
            return 0

        finished = next(task for task in done if task in exit_tasks)
        name = exit_tasks[finished]
        code = finished.result()
        print(f"[railway] {name} exited with code {code}", flush=True)
        return code or 1
    finally:
        stop_task.cancel()
        for task in exit_tasks:
            task.cancel()
        await _shutdown(processes)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
