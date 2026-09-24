pragma Singleton
import QtQuick
import Quickshell
import Quickshell.Io
import "Model.js" as Model

// One hub per shell process. Omarchy mounts a bar per monitor and this widget
// allows several instances, so without a shared hub every copy would run its
// own helper process against the same feeds. Widgets subscribe to a key
// (stop + filters); the hub runs ttc.py once per key on a schedule and
// broadcasts the parsed document through updated().
//
// Searches and trip plans are one-shot requests with a token, so a panel can
// ignore results for a query it has since abandoned.
Item {
  id: hub

  readonly property string pluginDir: {
    var url = String(Qt.resolvedUrl("."))
    return decodeURIComponent(url.replace(/^file:\/\//, "").replace(/\/$/, ""))
  }
  readonly property string helper: pluginDir + "/ttc.py"
  readonly property int maxPayloadCharacters: 1048576

  // Routes table from data/stops.json, loaded once for glyph and colour lookups.
  property var routesTable: ({})
  property bool routesLoaded: false

  // key -> { subs:{token:intervalMs}, intervalMs, nextDue, data, lastUpdated, error, fetching, queued, args }
  property var _entries: ({})
  property var _queue: []
  property int _nextToken: 1
  property string _activeKey: ""

  signal updated(string key)
  signal fetchingChanged(string key)
  signal searchResults(int token, var results, string error)
  signal planResults(int token, var result)

  function entry(key) { return _entries[key] || null }

  function subscribe(key, args, intervalSec) {
    var e = _entries[key]
    if (!e) {
      e = { subs: {}, intervalMs: 0, nextDue: 0, data: Model.emptyData(), lastUpdated: 0, error: "", fetching: false, queued: false, args: args }
      _entries[key] = e
    }
    e.args = args
    var token = _nextToken++
    e.subs[token] = Math.max(15, intervalSec || 30) * 1000
    _remerge(e)
    scheduler.running = true
    if (!e.lastUpdated) requestNow(key)
    return token
  }

  function updateInterval(key, token, intervalSec) {
    var e = _entries[key]
    if (!e || e.subs[token] === undefined) return
    e.subs[token] = Math.max(15, intervalSec || 30) * 1000
    _remerge(e)
  }

  function unsubscribe(key, token) {
    var e = _entries[key]
    if (!e) return
    delete e.subs[token]
    if (Object.keys(e.subs).length > 0) { _remerge(e); return }
    delete _entries[key]
    var at = _queue.indexOf(key)
    if (at !== -1) _queue.splice(at, 1)
  }

  function _remerge(e) {
    var min = 0
    for (var t in e.subs) { var ms = e.subs[t]; if (min === 0 || ms < min) min = ms }
    e.intervalMs = min
    if (min > 0 && e.nextDue > 0) e.nextDue = Math.min(e.nextDue, (e.lastUpdated || Date.now()) + min)
  }

  function requestNow(key) {
    var e = _entries[key]
    if (!e || e.queued || (e.fetching && _activeKey === key)) return
    e.queued = true
    _queue.push(key)
    pump()
  }

  function refreshAll() {
    for (var key in _entries) requestNow(key)
  }

  // ---------------------------------------------------------------- departures
  function pump() {
    if (fetchProc.running || watchdog.running || _queue.length === 0) return
    var key = _queue.shift()
    var e = _entries[key]
    if (!e) { pump(); return }
    e.queued = false
    e.fetching = true
    _activeKey = key
    fetchProc.command = ["python3", helper, "departures"].concat(e.args)
    fetchProc.running = true
    watchdog.restart()
    fetchingChanged(key)
  }

  function finish(exitCode, text) {
    watchdog.stop()
    var key = _activeKey
    var e = _entries[key]
    _activeKey = ""
    if (e) {
      var parsed = parsePayload(text)
      if (parsed) {
        e.data = parsed
        e.error = parsed.ok ? (parsed.error || "") : (parsed.error || "helper failed")
      } else {
        e.error = exitCode === 0 ? "helper returned no data" : "helper exited " + exitCode
        if (!e.data) e.data = Model.emptyData()
      }
      e.lastUpdated = Date.now()
      e.fetching = false
      e.nextDue = Date.now() + (e.intervalMs || 30000)
      updated(key)
      fetchingChanged(key)
    }
    pump()
  }

  function parsePayload(text) {
    if (!text || text.length > maxPayloadCharacters) return null
    try {
      var parsed = JSON.parse(text)
      return parsed && typeof parsed === "object" ? parsed : null
    } catch (err) {
      return null
    }
  }

  Process {
    id: fetchProc
    stdout: StdioCollector { id: fetchOut; waitForEnd: true }
    stderr: StdioCollector { waitForEnd: true }
    onExited: function (exitCode) { Qt.callLater(function () { hub.finish(exitCode, fetchOut.text) }) }
  }

  Timer {
    id: watchdog
    interval: 25000
    onTriggered: { if (fetchProc.running) fetchProc.running = false; hub.finish(-1, "") }
  }

  Timer {
    id: scheduler
    interval: 1000
    repeat: true
    running: false
    onTriggered: {
      var now = Date.now()
      for (var key in hub._entries) {
        var e = hub._entries[key]
        if (!e.fetching && !e.queued && (e.nextDue === 0 || now >= e.nextDue)) hub.requestNow(key)
      }
    }
  }

  // ---------------------------------------------------------------- search
  property int _searchToken: 0
  property var _pendingSearch: null

  function search(query) {
    var token = ++_searchToken
    _pendingSearch = { token: token, query: String(query || "") }
    _startSearch()
    return token
  }

  function _startSearch() {
    if (searchProc.running || !_pendingSearch) return
    var req = _pendingSearch
    _pendingSearch = null
    searchProc.token = req.token
    if (req.query.trim() === "") {
      searchResults(req.token, [], "")
      return
    }
    searchProc.command = ["python3", helper, "search", "--limit", "10", req.query]
    searchProc.running = true
  }

  Process {
    id: searchProc
    property int token: 0
    stdout: StdioCollector { id: searchOut; waitForEnd: true }
    onExited: function (exitCode) {
      Qt.callLater(function () {
        var parsed = hub.parsePayload(searchOut.text)
        var results = parsed && parsed.ok ? (parsed.results || []) : []
        hub.searchResults(searchProc.token, results, parsed ? (parsed.error || "") : "search failed")
        hub._startSearch()
      })
    }
  }

  // ---------------------------------------------------------------- planning
  property int _planToken: 0

  function plan(fromCode, toCode) {
    var token = ++_planToken
    if (planProc.running) planProc.running = false
    planProc.token = token
    planProc.command = ["python3", helper, "plan", String(fromCode), String(toCode), "--max", "4"]
    planProc.running = true
    return token
  }

  Process {
    id: planProc
    property int token: 0
    stdout: StdioCollector { id: planOut; waitForEnd: true }
    onExited: function (exitCode) {
      Qt.callLater(function () {
        var parsed = hub.parsePayload(planOut.text)
        hub.planResults(planProc.token, parsed || { ok: false, error: "planner returned no data", itineraries: [] })
      })
    }
  }

  // ---------------------------------------------------------------- routes table
  FileView {
    id: stopsFile
    path: hub.pluginDir + "/data/stops.json"
    printErrors: false
    onLoaded: {
      try {
        var d = JSON.parse(text())
        hub.routesTable = d.routes || {}
      } catch (err) {
        hub.routesTable = {}
      }
      hub.routesLoaded = true
    }
    onLoadFailed: hub.routesLoaded = true
  }

  // The first read can race shell startup; one delayed reload self-corrects.
  Component.onCompleted: stopsFile.reload()
  Timer { interval: 2000; running: !hub.routesLoaded; onTriggered: stopsFile.reload() }
}
