"""Minimal, thread-safe lifecycle telemetry. No wallet payloads or credentials."""
from __future__ import annotations
import asyncio
import functools
import copy
import threading
import time
import uuid
from collections import deque

ROLES = {
    'pulse': ('Pulse', 'Mengawasi alur kerja tim'),
    'scout': ('Scout', 'Memproses payload wallet'),
    'echo': ('Echo', 'Memperbarui registry trader'),
    'cipher': ('Cipher', 'Mengolah bukti dan konfluensi'),
    'sentry': ('Sentry', 'Melengkapi data harga'),
    'atlas': ('Atlas', 'Memperbarui performa dan leaderboard'),
    'relay': ('Relay', 'Memproses antrean pengiriman'),
}

class OfficeTelemetry:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.lock = threading.RLock()
        self.session = uuid.uuid4().hex
        self.running = False
        self.updated_at = clock()
        self.seq = 0
        self.chains = []
        self.metrics = {}
        self.services = {}
        self.events = deque(maxlen=100)
        self.agents = {key: dict(active=0, completed=0, last=clock(), error=False,
                                 message='Menunggu engine.') for key in ROLES}

    def event(self, agent, state, message):
        self.seq += 1
        self.events.appendleft(dict(id=self.seq, agent=agent, state=state,
                                    message=message, at=self.clock()))

    def set_running(self, running):
        with self.lock:
            self.running = running
            self.updated_at = self.clock()
            self.event('pulse', 'busy' if running else 'offline',
                       'Engine berjalan.' if running else 'Engine berhenti.')

    def heartbeat(self):
        with self.lock:
            self.updated_at = self.clock()

    def begin(self, agent):
        with self.lock:
            a = self.agents[agent]
            a['active'] += 1
            a['error'] = False
            a['last'] = self.clock()
            a['message'] = ROLES[agent][1]
            if a['active'] == 1:
                self.event(agent, 'busy', a['message'])

    def end(self, agent, outcome='ok'):
        with self.lock:
            a = self.agents[agent]
            a['active'] = max(0, a['active'] - 1)
            a['last'] = self.clock()
            if outcome == 'ok':
                a['completed'] += 1
                a['message'] = 'Siklus selesai. Menunggu tugas berikutnya.'
                a['error'] = False
            elif outcome == 'error':
                a['error'] = True
                a['message'] = 'Siklus gagal. Periksa log engine lokal.'
            else:
                a['message'] = 'Siklus dibatalkan saat engine berhenti.'
            self.event(agent, 'error' if a['error'] else 'waiting', a['message'])

    def snapshot(self):
        with self.lock:
            now = self.clock()
            fresh = now - self.updated_at < 8
            running = self.running and fresh
            result = []
            working = any(a['active'] for key, a in self.agents.items() if key != 'pulse')
            for key, a in self.agents.items():
                if not running:
                    state, message = 'offline', 'Engine tidak aktif atau heartbeat terputus.'
                elif key == 'pulse':
                    state = 'busy' if working else 'waiting'
                    message = 'Mengawasi modul aktif.' if working else 'Tim siap. Menunggu aktivitas engine.'
                elif a['active']:
                    state, message = 'busy', a['message']
                elif a['error']:
                    state, message = 'error', a['message']
                elif now - a['last'] > 14:
                    state, message = 'resting', 'Tidak ada siklus aktif. Rehat sambil menunggu.'
                else:
                    state, message = 'waiting', a['message']
                result.append(dict(id=key, state=state, message=message, completed=a['completed']))
            return dict(schema='r4ise.office.v1', session=self.session, running=running,
                        updated_at=self.updated_at, agents=result, events=list(self.events),
                        chains=[dict(c, poller_active=c['poller_active'] and running) for c in self.chains],
                        metrics=dict(self.metrics), services=copy.deepcopy(self.services))

    def wrap(self, obj, attr, agent):
        """Wrap only the running instance/module; caller can restore the exact method."""
        original = getattr(obj, attr)
        @functools.wraps(original)
        async def tracked(*args, **kwargs):
            self.begin(agent)
            try:
                result = await original(*args, **kwargs)
            except asyncio.CancelledError:
                self.end(agent, 'cancelled')
                raise
            except Exception:
                self.end(agent, 'error')
                raise
            else:
                self.end(agent)
                return result
        setattr(obj, attr, tracked)
        return lambda: setattr(obj, attr, original)
