"""Tiny protobuf text-format reader used only by the tests.

It turns the TTC's `?format=text` / `?debug` output into the same nested
dict shape that ttc.decode() produces, so the binary decoder can be checked
against the official text rendering of the same feed.
"""
import re

REPEATED = {"entity", "stop_time_update", "active_period", "informed_entity", "translation"}
_TOKEN = re.compile(r'\s*(?:(?P<close>\})|(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*(?P<value>"(?:[^"\\]|\\.)*"|[-+A-Za-z0-9_.]+)|(?P<open>\{)))')


def _unescape(s):
    return bytes(s, "utf-8").decode("unicode_escape").encode("latin-1").decode("utf-8")


def _coerce(name, raw):
    if raw.startswith('"'):
        return _unescape(raw[1:-1])
    if re.fullmatch(r"[-+]?\d+", raw):
        return int(raw)
    if re.fullmatch(r"[-+]?\d*\.\d+(e[-+]?\d+)?", raw, re.I):
        return round(float(raw), 6)
    return raw


def parse(text):
    root = {}
    stack = [root]
    pos = 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            if text[pos:].strip() == "":
                break
            raise ValueError(f"unexpected text at {pos}: {text[pos:pos+40]!r}")
        pos = m.end()
        if m.group("close"):
            stack.pop()
            continue
        name = m.group("name")
        if m.group("open"):
            child = {}
            _put(stack[-1], name, child)
            stack.append(child)
        else:
            _put(stack[-1], name, _coerce(name, m.group("value")))
    return root


def _put(obj, name, value):
    if name in REPEATED:
        obj.setdefault(name, []).append(value)
    else:
        obj[name] = value
