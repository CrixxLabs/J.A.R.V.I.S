import json
import threading

import tasks


def test_task_and_reminder_workers_stop(tmp_path, monkeypatch):
    task_file = tmp_path / "tasks.json"
    task_file.write_text(json.dumps([]), encoding="utf-8")
    monkeypatch.setattr(tasks, "TASKS_FILE", str(task_file))

    tasks.init(speak_fn=lambda _text: None, action_fn=lambda _action: None)
    tasks.add_reminder("do not fire", seconds=3600)
    task_worker = tasks._task_thread
    reminder_workers = list(tasks._reminder_threads.values())

    tasks.stop_tasks()

    assert task_worker is not None and not task_worker.is_alive()
    assert reminder_workers and all(not worker.is_alive() for worker in reminder_workers)
    assert not any(thread.name == "jarvis-task-worker" and thread.is_alive()
                   for thread in threading.enumerate())
