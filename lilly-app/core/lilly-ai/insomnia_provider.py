"""Stub InsomniaProvider — placeholder for training_control import."""


class InsomniaProvider:
    def __init__(self, run_shell=None):
        self._run_shell = run_shell

    def status(self):
        return {"ok": False, "error": "insomnia_provider not available"}

    def run(self, body=None):
        return {"ok": False, "error": "insomnia_provider not available"}

    def stop(self, session_id=None):
        return {"ok": False, "error": "insomnia_provider not available"}
