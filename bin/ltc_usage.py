"""Read local token counters incrementally. Only accounting metadata survives parsing."""
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import blake2s
import json
import os
from pathlib import Path
import re
import threading
import time

from ltc_usage_prices import COMPONENTS, VERSION, estimate

UNKNOWN = 'Model nieznany'
FIELDS = ('fresh', 'read', 'write', 'output', 'reasoning')
# Skip the common content-heavy envelope before decoding the body. Other
# layouts still take the normal JSON parser path.
CONTENT_ROW = re.compile(rb'^\s*\{\s*"timestamp"\s*:\s*"[^"\\]*"\s*,\s*(?:"ordinal"\s*:\s*\d+\s*,\s*)?"type"\s*:\s*"response_item"\s*,')


def source_roots():
    codex = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex')
    claude = Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude')
    return {'Codex': [codex / 'sessions', codex / 'archived_sessions'],
            'Claude Code': [claude / 'projects']}


def identifier(value):
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError('missing identifier')
    return blake2s(value.encode(), digest_size=16).digest()


def model_name(value):
    if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}(?:\[[0-9]+[kKmM]\])?', value):
        return value
    return UNKNOWN


def instant(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('missing timezone')
    return stamp.timestamp()


def counters(usage, claude=False):
    if not isinstance(usage, dict):
        raise ValueError('missing usage')
    inp, out = usage['input_tokens'], usage['output_tokens']
    if claude:
        read = usage.get('cache_read_input_tokens', 0)
        write = usage.get('cache_creation_input_tokens', 0)
        reasoning = (usage.get('output_tokens_details') or {}).get('thinking_tokens', 0)
    else:
        read = usage.get('cached_input_tokens', 0)
        write = usage.get('cache_write_input_tokens', 0)
        reasoning = usage.get('reasoning_output_tokens', 0)
    if not all(type(n) is int and n >= 0 for n in (inp, read, write, out, reasoning)):
        raise ValueError('invalid token count')
    fresh = inp if claude else inp - read - write
    if fresh < 0 or reasoning > out:
        raise ValueError('invalid token subsets')
    return (fresh, read, write, out, reasoning)


@dataclass
class FileState:
    identity: tuple
    offset: int = 0
    size: int = -1
    mtime: int = 0
    anchor: bytes = b''
    session: bytes = b''
    model: str = UNKNOWN
    turns: dict = field(default_factory=dict)
    previous: tuple = ()
    last_snapshot: tuple = ()
    fork: bool = False
    structured: bool = False
    skipped: int = 0
    events: dict = field(default_factory=dict)

    def record(self, key, stamp, model, counts, write_split=(0, 0), single=True):
        # Streaming Claude blocks can update the same response. Retain the earliest
        # timestamp and the largest final counters, including across copied files.
        old = self.events.get(key)
        self.events[key] = combine(old, (stamp, model, counts, write_split, single))

    def codex(self, row):
        kind = row.get('type')
        p = row.get('payload')
        if not isinstance(p, dict):
            return
        if kind == 'session_meta':
            self.session = identifier(p.get('id') or p.get('session_id'))
            self.fork = bool(p.get('forked_from_id') or p.get('history_base') or p.get('parent_thread_id'))
            self.previous = ()
            self.last_snapshot = ()
            self.structured = False
            self.turns.clear()
            self.model = UNKNOWN
        elif kind == 'turn_context':
            self.model = model_name(p.get('model'))
            if p.get('turn_id'):
                self.turns[identifier(p['turn_id'])] = self.model
        elif kind == 'token_usage_record':
            # Some format transitions log the matching snapshot first.
            if not self.structured and self.last_snapshot and p.get('thread_token_usage'):
                if self.last_snapshot[1] == counters(p['thread_token_usage']):
                    self.events.pop(self.last_snapshot, None)
            self.structured = True
            model = self.turns.get(identifier(p['turn_id']), self.model) if p.get('turn_id') else self.model
            counts = counters(p['usage'])
            retention = p['usage'].get('prompt_cache_retention') or p.get('prompt_cache_retention')
            split = {'30m': (counts[2], 0), '12h': (0, counts[2])}.get(retention, (0, 0)) if isinstance(retention, str) else (0, 0)
            self.record(identifier(p.get('response_id')), instant(row['timestamp']),
                        model_name(p['model']) if p.get('model') else model, counts, split)
        elif kind == 'event_msg' and p.get('type') == 'token_count' and not self.structured:
            info = p.get('info')
            if isinstance(info, dict) and info.get('total_token_usage'):
                self.snapshot(row['timestamp'], info)

    def snapshot(self, timestamp, info):
        total = counters(info['total_token_usage'])
        previous, self.previous = self.previous, total
        if previous == total:
            return
        last = counters(info['last_token_usage']) if info.get('last_token_usage') else None
        if previous and all(a >= b for a, b in zip(total, previous)):
            delta = tuple(a - b for a, b in zip(total, previous))
        elif total == last and (previous or not self.fork):
            delta = last
        else:
            # First inherited snapshot or a discontinuity. Establish a baseline.
            return
        if self.session and delta[4] <= delta[3]:
            stamp = instant(timestamp)
            self.last_snapshot = (self.session, total, stamp)
            self.record(self.last_snapshot, stamp, self.model, delta, single=(delta == last))

    def parse(self, line, harness):
        if harness == 'Codex' and CONTENT_ROW.match(line, 0, 256):
            return
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError('invalid row')
            if harness == 'Codex':
                self.codex(row)
            elif row.get('type') == 'assistant':
                msg = row.get('message') or {}
                if msg.get('usage') is not None and msg.get('model') != '<synthetic>':
                    counts = counters(msg['usage'], claude=True)
                    split = msg['usage'].get('cache_creation')
                    writes = (0, 0)
                    if isinstance(split, dict):
                        parts = (split.get('ephemeral_5m_input_tokens', 0), split.get('ephemeral_1h_input_tokens', 0))
                        if all(type(n) is int and n >= 0 for n in parts) and sum(parts) <= counts[2]:
                            writes = parts
                    self.record(identifier(msg.get('id')), instant(row['timestamp']),
                                model_name(msg.get('model')), counts, writes)
        except (AttributeError, ValueError, KeyError, TypeError, OverflowError, RecursionError):
            self.skipped += 1


def combine(old, new):
    if old is None or old == new:
        return new
    stamp, model, values, split, single = new
    return (min(old[0], stamp), old[1] if old[1] != UNKNOWN else model,
            tuple(max(a, b) for a, b in zip(old[2], values)), max(old[3], split, key=sum), old[4] or single)


def anchor_at(stream, offset):
    length = min(256, offset)
    stream.seek(offset - length)
    return blake2s(stream.read(length), digest_size=16).digest()


def read_file(path, harness, state):
    stat = path.stat()
    if state and (state.identity, state.size, state.mtime) == ((stat.st_dev, stat.st_ino), stat.st_size, stat.st_mtime_ns):
        return state
    with path.open('rb') as stream:
        stat = os.fstat(stream.fileno())
        identity = (stat.st_dev, stat.st_ino)
        if (not state or state.identity != identity or stat.st_size <= state.size
                or anchor_at(stream, state.offset) != state.anchor):
            state = FileState(identity)
        stream.seek(state.offset)
        # Freeze the read boundary so an active writer cannot monopolize refresh.
        while state.offset < stat.st_size:
            line = stream.readline(stat.st_size - state.offset)
            if not line.endswith(b'\n'):
                break
            state.parse(line, harness)
            state.offset += len(line)
        state.anchor = anchor_at(stream, state.offset)
        state.size, state.mtime = stat.st_size, stat.st_mtime_ns
        return state


class UsageIndex:
    def __init__(self, roots=None, ttl=60):
        self.roots = source_roots() if roots is None else roots
        self.ttl = ttl
        self.files = {}
        self.lock = threading.Lock()
        self.expires = 0
        self.result = None

    def refresh(self):
        files = {}
        sources = {}
        for harness, roots in self.roots.items():
            errors = []
            seen = 0
            for root in roots:
                if not root.is_dir():
                    continue
                seen += 1
                for parent, dirs, names in os.walk(root, onerror=errors.append):
                    for name in names:
                        if not name.endswith('.jsonl'):
                            continue
                        path = Path(parent) / name
                        if path.is_symlink():
                            continue
                        key = (harness, path)
                        try:
                            files[key] = read_file(path, harness, self.files.get(key))
                        except OSError as error:
                            errors.append(error)
            sources[harness] = {'available': bool(seen), 'errors': len(errors), 'files': 0, 'skipped': 0}
        self.files = files
        rows = []
        for harness, status in sources.items():
            unique = {}
            for (source, _), state in files.items():
                if source != harness:
                    continue
                status['files'] += 1
                status['skipped'] += state.skipped
                for key, event in state.events.items():
                    unique[key] = combine(unique.get(key), event)
            groups = defaultdict(Counter)
            for stamp, model, counts, split, single in unique.values():
                day = datetime.fromtimestamp(stamp).date().isoformat()
                group = groups[day, model]
                group.update(dict(zip(FIELDS, counts)))
                costs = estimate(model, counts, split, single)
                if costs is not None:
                    group['priced_requests'] += 1
                    group['priced_tokens'] += sum(counts[:4])
                    group['assumed_write_tokens'] += counts[2] - sum(split)
                    for key, cost in zip(COMPONENTS, costs):
                        group['nano_' + key] += cost
            for (day, model), counts in sorted(groups.items()):
                usd = None
                if counts['priced_requests']:
                    usd = {key: counts['nano_' + key] / 1_000_000_000 for key in COMPONENTS}
                rows.append(dict(day=day, harness=harness, model=model, usd=usd,
                                 priced_tokens=counts['priced_tokens'], assumed_write_tokens=counts['assumed_write_tokens'],
                                 **{key: counts[key] for key in FIELDS}))
        now = datetime.now().astimezone()
        return dict(pricing_version=VERSION, rows=rows, sources=sources, today=now.date().isoformat(),
                    updated=now.isoformat(timespec='seconds'), zone=now.tzname())

    def snapshot(self):
        with self.lock:
            if self.result is None or time.monotonic() >= self.expires or self.result['today'] != datetime.now().date().isoformat():
                self.result = self.refresh()
                self.expires = time.monotonic() + self.ttl
            return self.result
