"""Bounded asynchronous local JSONL diagnostics, never camera frames."""
import json
import logging
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from pathlib import Path
import queue
import time


class SessionLog:
    def __init__(self,directory):
        path=Path(directory);path.mkdir(parents=True,exist_ok=True)
        self.handler=RotatingFileHandler(path/'session.jsonl',maxBytes=2_000_000,backupCount=3,encoding='utf-8')
        self.handler.setFormatter(logging.Formatter('%(message)s'))
        self.queue=queue.Queue(maxsize=256)
        self.logger=logging.getLogger(f'airpad.{id(self)}')
        self.logger.setLevel(logging.INFO);self.logger.propagate=False
        self.logger.addHandler(QueueHandler(self.queue))
        self.listener=QueueListener(self.queue,self.handler)
        self.listener.start()
        self._closed=False

    def event(self,kind,**fields):
        if self.queue.full():return
        self.logger.info(json.dumps({'time':time.time(),'event':kind,**fields},ensure_ascii=False,allow_nan=False))

    def close(self):
        # A full diagnostics queue must not prevent resource shutdown.
        while self.queue.full():
            try:self.queue.get_nowait()
            except queue.Empty:break
        # Shutdown may legitimately be requested twice (tray Exit *and* window close).
        # Raising here would abort whatever cleanup follows, so make it idempotent.
        if self._closed:return
        self._closed=True
        self.listener.stop();self.handler.close();self.logger.handlers.clear()
