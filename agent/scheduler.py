"""Persistent interval scheduler with overlap protection."""
import threading
import time


class RunScheduler:
    def __init__(self,store,runner,poll_seconds=5): self.store=store; self.runner=runner; self.poll_seconds=max(1,poll_seconds); self._stop=threading.Event(); self._thread=None
    def tick(self,now=None):
        completed=[]
        for schedule in self.store.due_schedules(now):
            status='completed'
            try: self.runner(schedule)
            except Exception: status='failed'
            self.store.finish_schedule(schedule['id'],status,now); completed.append((schedule['id'],status))
        return completed
    def start(self):
        if self._thread and self._thread.is_alive(): return
        def loop():
            while not self._stop.wait(self.poll_seconds): self.tick()
        self._stop.clear(); self._thread=threading.Thread(target=loop,daemon=True,name='thor-scheduler'); self._thread.start()
    def stop(self): self._stop.set()
