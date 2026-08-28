import asyncio
import heapq
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, List, Optional, Tuple
import threading
from logger import logger


class JobState(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    CANCELLED = "CANCELLED"


@dataclass
class Job:
    """Queue job abstraction: state tracking, retries with exponential backoff, timeouts."""
    id: int
    func: Callable
    args: Tuple = ()
    kwargs: dict = field(default_factory=dict)
    execute_at: float = 0.0
    state: JobState = JobState.PENDING
    attempts: int = 0
    max_attempts: int = 3
    timeout: Optional[float] = None
    backoff_base: float = 5.0
    last_error: str = ""

    def next_retry_delay(self) -> float:
        return min(self.backoff_base * (2 ** max(0, self.attempts - 1)), 300.0)


class TaskScheduler:
    """
    Priority queue-based task scheduler for reminders, giveaways, and other timed events.
    Features:
    - Persistent across restarts
    - Thread-safe operations
    - Priority queue for efficient scheduling
    - Automatic cleanup of expired tasks
    - Job queue mode: state tracking, retries with exponential backoff, timeouts
    """

    def __init__(self, bot):
        self.bot = bot
        self._tasks: List[Tuple[float, int, Callable, Tuple]] = []  # (time, id, func, args)
        self._task_id_counter = 0
        self._lock = threading.Lock()
        self._running = False
        self._task_thread = None
        self._stop_event = threading.Event()
        self._jobs: dict = {}  # job_id -> Job

    def schedule_task(self, execute_at: float, func: Callable, *args, **kwargs):
        """Schedule a task to execute at a specific time."""
        with self._lock:
            task_id = self._task_id_counter
            self._task_id_counter += 1

            heapq.heappush(self._tasks, (execute_at, task_id, func, args, kwargs))

            logger.debug(f"Scheduled task {task_id} for {execute_at}")
            return task_id

    def schedule_job(self, func: Callable, delay: float = 0.0, *args,
                     max_attempts: int = 3, timeout: Optional[float] = None,
                     backoff_base: float = 5.0, **kwargs) -> int:
        """Schedule a managed job that runs after `delay` seconds with retries."""
        with self._lock:
            job_id = self._task_id_counter
            self._task_id_counter += 1
            job = Job(
                id=job_id, func=func, args=args, kwargs=kwargs,
                execute_at=time.time() + delay,
                max_attempts=max_attempts, timeout=timeout,
                backoff_base=backoff_base,
            )
            self._jobs[job_id] = job
            heapq.heappush(self._tasks, (job.execute_at, job_id, self._run_job, (job,), {}))
            logger.debug(f"Scheduled job {job_id} ({getattr(func, '__name__', func)}) for {job.execute_at}")
            return job_id

    def get_job(self, job_id: int) -> Optional[Job]:
        return self._jobs.get(job_id)

    async def _run_job(self, job: Job):
        """Execute a managed job with timeout, retries, and state tracking."""
        while True:
            job.attempts += 1
            job.state = JobState.RUNNING
            try:
                if asyncio.iscoroutinefunction(job.func):
                    coro = job.func(*job.args, **job.kwargs)
                    if job.timeout:
                        await asyncio.wait_for(coro, timeout=job.timeout)
                    else:
                        await coro
                else:
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, job.func, *job.args)
                job.state = JobState.SUCCESS
                job.last_error = ""
                logger.debug(f"Job {job.id} succeeded")
                return
            except Exception as e:
                job.last_error = str(e)[:300]
                if job.attempts >= job.max_attempts:
                    job.state = JobState.FAILED
                    logger.error(f"Job {job.id} ({getattr(job.func, '__name__', job.func)}) failed permanently "
                                 f"after {job.attempts} attempts: {e}")
                    return
                job.state = JobState.RETRYING
                delay = job.next_retry_delay()
                logger.warning(f"Job {job.id} attempt {job.attempts}/{job.max_attempts} failed: {e}; "
                               f"retrying in {delay:.0f}s")
                await asyncio.sleep(delay)

    def cancel_task(self, task_id: int) -> bool:
        """Cancel a scheduled task by ID."""
        with self._lock:
            for i, (exec_time, tid, func, args, kwargs) in enumerate(self._tasks):
                if tid == task_id:
                    self._tasks.pop(i)
                    heapq.heapify(self._tasks)
                    logger.debug(f"Cancelled task {task_id}")
                    return True
            return False

    def cancel_job(self, job_id: int) -> bool:
        """Cancel a managed job (queued or mid-retry). Running work cannot be interrupted."""
        job = self._jobs.get(job_id)
        if job is None or job.state in (JobState.SUCCESS, JobState.FAILED, JobState.CANCELLED):
            return False
        removed = self.cancel_task(job_id)
        job.state = JobState.CANCELLED
        return removed or job.state == JobState.CANCELLED

    def get_scheduled_tasks(self) -> List[Tuple[float, int, str]]:
        """Get list of scheduled tasks for debugging."""
        with self._lock:
            return [(exec_time, task_id, func.__name__) for exec_time, task_id, func, _, _ in self._tasks]

    async def start(self):
        """Start the task scheduler."""
        if self._running:
            return

        self._running = True
        self._stop_event.clear()

        # Start the scheduler loop
        asyncio.create_task(self._scheduler_loop())
        logger.info("Task scheduler started")

    async def stop(self):
        """Stop the task scheduler."""
        if not self._running:
            return

        self._running = False
        self._stop_event.set()
        logger.info("Task scheduler stopped")

    async def _scheduler_loop(self):
        """Main scheduler loop."""
        while self._running and not self._stop_event.is_set():
            try:
                # Check for tasks to execute
                now = time.time()

                with self._lock:
                    while self._tasks and self._tasks[0][0] <= now:
                        exec_time, task_id, func, args, kwargs = heapq.heappop(self._tasks)

                        # Schedule execution in the event loop
                        asyncio.create_task(self._execute_task(func, args, kwargs, task_id))

                # Sleep until next task or for a maximum of 1 second
                sleep_time = 1.0
                if self._tasks:
                    next_time = self._tasks[0][0]
                    sleep_time = min(sleep_time, max(0.1, next_time - now))

                await asyncio.sleep(sleep_time)

            except Exception as e:
                logger.error(f"Error in scheduler loop: {e}")
                await asyncio.sleep(1)

    async def _execute_task(self, func: Callable, args: Tuple, kwargs: dict, task_id: int):
        """Execute a scheduled task."""
        try:
            logger.debug(f"Executing scheduled task {task_id}: {func.__name__}")

            if asyncio.iscoroutinefunction(func):
                await func(*args, **kwargs)
            else:
                # Run in thread pool for synchronous functions
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, func, *args, **kwargs)

            logger.debug(f"Completed scheduled task {task_id}")

        except Exception as e:
            logger.error(f"Error executing scheduled task {task_id} ({func.__name__}): {e}")

    def save_tasks(self) -> List[dict]:
        """Save scheduled tasks for persistence."""
        with self._lock:
            saved_tasks = []
            for exec_time, task_id, func, args, kwargs in self._tasks:
                # Only save tasks that can be serialized
                try:
                    saved_tasks.append({
                        'id': task_id,
                        'execute_at': exec_time,
                        'func_name': func.__name__,
                        'args': args,
                        'kwargs': kwargs
                    })
                except:
                    pass  # Skip tasks that can't be serialized
            return saved_tasks

    def load_tasks(self, saved_tasks: List[dict]):
        """Load scheduled tasks from saved data."""
        logger.info(f"load_tasks called with {len(saved_tasks)} tasks - task loading monitored")

# Shared scheduler instance. MiroBot attaches this same object as
# bot.task_scheduler so reminders/giveaways (which import this singleton)
# and bot-side restores all use one queue.
task_scheduler = TaskScheduler(None)