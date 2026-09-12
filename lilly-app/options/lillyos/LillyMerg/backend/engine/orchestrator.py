import threading
import time
from engine.store import DataStore
from engine.workflow import WorkflowEngine
from engine.tasklet_runner import TaskletRunner
from agents.ollama import OllamaRunner
from chat.lilly import LillyChat


class Orchestrator:
    def __init__(self):
        self.store = DataStore()
        self.workflow = WorkflowEngine(self.store)
        self.ollama = OllamaRunner(self.store, self.workflow)
        self.tasklets = TaskletRunner(self.store, self.workflow)
        self.lilly = LillyChat(self.store, self.workflow)
        self._running = False
        self._thread = None

    def start(self):
        self._running = True
        self.ollama.start()
        self.tasklets.start()
        self._thread = threading.Thread(target=self._tick_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self.ollama.stop()
        self.tasklets.stop()

    def _tick_loop(self):
        while self._running:
            try:
                self.workflow.run_automation()
            except Exception:
                pass
            time.sleep(3)
