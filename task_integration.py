"""
task_integration.py — Task Queue Integration for JARVIS MARK VII

Bridges the planner/executor with the async task queue.
Determines which actions should be queued vs executed synchronously.
Provides progress callbacks and result handling.
"""

import asyncio
import time
from typing import Dict, Any, Optional, Callable, Awaitable, List
from dataclasses import dataclass, field
from enum import Enum

from task_queue import (
    TaskQueue, Task, TaskStatus, TaskPriority,
    get_task_queue, init_task_queue, shutdown_task_queue,
    submit_task, wait_for_task, cancel_task, get_task_status
)
from task_queue import Task as TaskQueueTask


class ExecutionMode(str, Enum):
    """How an action should be executed."""
    SYNC = "sync"           # Execute immediately in current thread
    ASYNC = "async"         # Queue to task queue for background processing
    STREAMED = "streamed"   # Queue with progress streaming


class ActionClassification(Enum):
    """Classification of action for execution strategy."""
    FOREGROUND = "foreground"      # Must run synchronously (UI interaction, confirmations)
    BACKGROUND = "background"      # Can run in background (file ops, downloads, analysis)
    LONG_RUNNING = "long_running"  # Long-running, should always queue (video gen, large file ops)
    GENERATIVE = "generative"      # AI generation (video, image, code)


# Actions that should ALWAYS run synchronously (foreground)
FOREGROUND_ACTIONS = {
    "open_app", "close_app", "join_meeting", "media", "scroll", "click",
    "type_text", "voice_type", "lock_pc", "shutdown_pc", "restart_pc",
    "system_info", "system_status", "datetime", "web_search", "summarize_url",
    "lock_pc", "shutdown_pc", "restart_pc", "media", "scroll", "click",
    "type_text", "voice_type", "read_screen", "screenshot_describe",
    "system_control", "datetime", "system_info", "system_status",
    "send_message", "send_email", "send_whatsapp",
    "lock_pc", "shutdown_pc", "restart_pc", "join_meeting",
    "save_login", "list_logins", "delete_login", "open_and_login",
}

# Actions that should be queued for background execution
BACKGROUND_ACTIONS = {
    "install_app", "install_and_login", "open_and_login",
    "web_search", "summarize_url", "send_email",
    "weather", "news", "calendar_today", "calendar_add",
    "spotify_play", "spotify_control",
    "open_file", "list_folder", "search_file", "rename_file",
    "run_sequence", "whatsapp_download", "whatsapp_read",
    "whatsapp_timetable_update", "morning_briefing",
    "voice_type", "clipboard_read", "clipboard_write",
    "spotify_play", "spotify_control",
    "calendar_today", "calendar_add",
    # File Processor actions
    "process_file", "list_uploaded_files",
    # Vision/Eyes actions
    "capture_webcam", "capture_screen_region", "analyze_image_file", "read_image_text", "vision_status",
}

# Actions that are long-running and should always be queued
LONG_RUNNING_ACTIONS = {
    "generate_video", "generate_image",
    "install_app", "install_and_login",
    "portal_scan", "portal_scan_describe",
    "run_diagnostic", "run_sequence",
    "self_scan", "self_capabilities", "self_changes",
}

# Generative actions that produce artifacts
GENERATIVE_ACTIONS = {
    "generate_video", "generate_image",
    "install_app", "install_and_login",
    "open_and_login",
}

# Actions that produce files/artifacts
ARTIFACT_PRODUCING_ACTIONS = {
    "generate_video", "generate_image",
    "install_app", "install_and_login",
    "open_and_login",
    "save_login", "list_logins", "delete_login",
    "portal_scan", "save_login",
}

# Actions that support progress callbacks
PROGRESS_TRACKED_ACTIONS = {
    "install_app", "install_and_login", "generate_video", "generate_image",
    "portal_scan", "run_diagnostic", "run_sequence",
    "install_app", "install_and_login", "open_and_login",
    "process_file",
    "capture_webcam", "analyze_image_file",
}

# Actions that must run synchronously (never queue)
NEVER_QUEUE_ACTIONS = {
    "open_app", "close_app", "join_meeting", "media", "scroll", "click",
    "type_text", "voice_type", "lock_pc", "shutdown_pc", "restart_pc",
    "system_info", "system_status", "datetime", "web_search", "summarize_url",
    "read_screen", "screenshot_describe", "system_control", "datetime",
    "system_info", "system_status", "send_message", "send_email", "send_whatsapp",
    "lock_pc", "shutdown_pc", "restart_pc", "join_meeting",
    "save_login", "list_logins", "delete_login", "open_and_login",
    "lock_pc", "shutdown_pc", "restart_pc", "join_meeting",
    "save_login", "list_logins", "delete_login", "open_and_login",
    "list_uploaded_files",
}


class ExecutionMode(str, Enum):
    """How an action should be executed."""
    SYNC = "sync"           # Execute immediately in current thread
    ASYNC = "async"         # Queue to task queue for background processing
    STREAMED = "streamed"   # Queue with progress streaming


@dataclass
class ExecutionPlan:
    """Plan for how to execute an action."""
    mode: str  # "sync", "async", "streamed"
    action: Dict[str, Any]
    reason: str
    priority: int = 20  # TaskPriority.NORMAL
    callback: Optional[Callable] = None
    timeout: Optional[float] = None
    max_retries: int = 1


class TaskIntegration:
    """
    Bridges the planner/executor with the async task queue.
    
    Responsibilities:
    - Classify actions for execution mode
    - Submit actions to task queue
    - Handle progress callbacks
    - Manage task lifecycle
    """
    
    def __init__(self, task_queue: Optional['TaskQueue'] = None):
        self.task_queue = task_queue or get_task_queue()
        self._progress_callbacks: Dict[str, Callable] = {}
        self._completion_callbacks: Dict[str, Callable] = {}
    
    def classify_action(self, action: Dict[str, Any]) -> ExecutionPlan:
        """
        Determine how an action should be executed.
        
        Returns an ExecutionPlan with mode and metadata.
        """
        if not isinstance(action, dict):
            action_name = str(action)
        else:
            action_name = action.get("action", "unknown")
        
        # Check if action should never be queued
        if action_name in NEVER_QUEUE_ACTIONS:
            return ExecutionPlan(
                mode="sync",
                action=action,
                reason=f"Action '{action_name}' must run synchronously (foreground operation)",
                priority=10  # HIGH
            )
        
        # Check for long-running actions
        if action_name in LONG_RUNNING_ACTIONS:
            return ExecutionPlan(
                mode="async",
                action=action,
                reason=f"Action '{action_name}' is long-running, queuing for background execution",
                priority=20,  # NORMAL
                timeout=600.0,  # 10 minutes
                max_retries=2
            )
        
        # Check for generative actions
        if action_name in GENERATIVE_ACTIONS:
            return ExecutionPlan(
                mode="async",
                action=action,
                reason=f"Action '{action_name}' is generative, queuing for background processing",
                priority=20,  # NORMAL
                timeout=300.0,  # 5 minutes
                max_retries=1
            )
        
        # Check for artifact-producing actions
        if action_name in ARTIFACT_PRODUCING_ACTIONS:
            return ExecutionPlan(
                mode="async",
                action=action,
                reason=f"Action '{action_name}' produces artifacts, queuing for background",
                priority=20,
                timeout=300.0,
                max_retries=1
            )
        
        # Check for background actions
        if action_name in BACKGROUND_ACTIONS:
            return ExecutionPlan(
                mode="async",
                action=action,
                reason=f"Action '{action_name}' suitable for background execution",
                priority=20,
                timeout=120.0,
                max_retries=1
            )
        
        # Default to synchronous for unclassified actions
        return ExecutionPlan(
            mode="sync",
            action=action,
            reason=f"Action '{action_name}' not classified for background, executing synchronously",
            priority=20
        )
    
    def should_queue(self, action: Dict[str, Any]) -> bool:
        """Quick check if action should be queued."""
        plan = self.classify_action(action)
        return plan.mode != "sync"
    
    def get_execution_mode(self, action: Dict[str, Any]) -> str:
        """Get execution mode string for an action."""
        return self.classify_action(action).mode
    
    async def submit_action(
        self,
        action: Dict[str, Any],
        speak_fn: Optional[Callable[[str], Awaitable[None]]] = None,
        progress_callback: Optional[Callable[[str, float], Awaitable[None]]] = None,
        completion_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> Dict[str, Any]:
        """
        Submit an action for execution (sync or async based on classification).
        
        Returns task_id for async operations, or executes synchronously.
        """
        plan = self.classify_action(action)
        
        if plan.mode == "sync":
            # Execute synchronously
            from executor import execute_with_retry
            result = execute_with_retry(action)
            return {"mode": "sync", "result": result}
        
        # Async execution - submit to task queue
        task_id = await self._submit_async(action, plan, progress_callback, completion_callback)
        return {"mode": "async", "task_id": task_id}
    
    async def _submit_async(
        self,
        action: Dict[str, Any],
        plan: 'ExecutionPlan',
        progress_callback: Optional[Callable] = None,
        completion_callback: Optional[Callable] = None,
    ) -> str:
        """Submit action to task queue for async execution."""
        from task_queue import submit_task, TaskPriority
        
        action_name = action.get("action", "unknown")
        plan = self.classify_action(action)
        
        # Create coroutine factory
        async def _coro_factory():
            return await self._execute_action_async(action, plan)
        
        task_id = await submit_task(
            coro_factory=lambda: self._execute_action_async(action, plan),
            name=f"action_{action_name}",
            priority=TaskPriority(plan.priority)
        )
        
        # Register callbacks
        return task_id
    
    async def _execute_action_async(
        self,
        action: Dict[str, Any],
        plan: 'ExecutionPlan'
    ) -> Dict[str, Any]:
        """Execute action asynchronously with progress reporting."""
        from executor import execute_with_retry
        from task_queue import get_task_queue, TaskStatus
        
        action_name = action.get("action", "unknown")
        task_queue = get_task_queue()
        
        # Find our task to update progress
        task = None
        for task_id, task_obj in task_queue._tasks.items():
            if action.get("action") in task_obj.name:
                task = task_obj
                break
        
        # Execute with retries
        max_retries = plan.max_retries
        try:
            for attempt in range(max_retries + 1):
                if task and task.cancel_event.is_set():
                    return {"success": False, "error": "Cancelled", "cancelled": True}
                
                from executor import execute_with_retry
                result = execute_with_retry(action)
                
                if result[0]:  # Success
                    if task:
                        task.status = TaskStatus.COMPLETED
                        task.progress = 1.0
                        task.result = result
                        task.completed_at = time.time()
                    return {"success": True, "result": result}
                
                # Failed - check if we should retry
                if attempt < max_retries:
                    # Update progress
                    if task:
                        task.status = TaskStatus.RETRYING
                        task.retry_count = attempt + 1
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff
                    continue
                
                # Max retries exceeded
                if task:
                    task.status = TaskStatus.FAILED
                    task.error = result[1] or "Max retries exceeded"
                return {"success": False, "error": result[1] or "Max retries exceeded"}
        except Exception as exc:
            return {"success": False, "error": f"Execution error: {exc}"}
        
        return {"success": False, "error": "Max retries exceeded"}
    
    def register_progress_callback(self, task_id: str, callback: Callable) -> None:
        """Register a progress callback for a task."""
        # Progress callbacks can be stored for UI updates
        pass
    
    def register_completion_callback(self, task_id: str, callback: Callable) -> None:
        """Register a completion callback for a task."""
        pass
    
    async def wait_for_completion(self, task_id: str, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Wait for a task to complete and return result."""
        from task_queue import wait_for_task
        return await wait_for_task(task_id, timeout)
    
    def cancel_task(self, task_id: str) -> bool:
        """Cancel a running task."""
        from task_queue import cancel_task
        return asyncio.run(cancel_task(task_id))
    
    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get status of a task."""
        from task_queue import get_task_status
        return asyncio.run(get_task_status(task_id))


# Global task integration instance
_task_integration: Optional[TaskIntegration] = None


def get_task_integration() -> TaskIntegration:
    """Get global task integration instance."""
    global _task_integration
    if _task_integration is None:
        _task_integration = TaskIntegration()
    return _task_integration


def init_task_integration(task_queue=None) -> TaskIntegration:
    """Initialize the global task integration."""
    global _task_integration
    _task_integration = TaskIntegration(task_queue)
    return _task_integration


def classify_action_for_execution(action: Dict[str, Any]) -> str:
    """Quick function to get execution mode for an action."""
    return get_task_integration().classify_action(action).mode


def should_queue_action(action: Dict[str, Any]) -> bool:
    """Check if an action should be queued."""
    return get_task_integration().should_queue(action)


async def submit_action_async(
    action: Dict[str, Any],
    speak_fn: Optional[Callable] = None,
    progress_callback: Optional[Callable] = None,
    completion_callback: Optional[Callable] = None,
) -> Dict[str, Any]:
    """Submit action for async execution."""
    return await get_task_integration().submit_action(
        action, speak_fn, progress_callback, completion_callback
    )


def classify_action(action: Dict[str, Any]) -> str:
    """Quick classification of action execution mode."""
    return get_task_integration().classify_action(action).mode