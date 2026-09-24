import QtQuick
import Quickshell
import qs.Commons
import qs.Ui
import "Model.js" as Model

// TTC Departures: next bus, streetcar, or subway arrivals for one Toronto stop
// in the bar, with a panel for the full board, alerts, a stop picker, and a
// trip planner.
//
//   left click    open the panel      middle click  refresh
//   right click   board as a notification
BarWidget {
  id: root
  moduleName: "io.github.mehrshadfb.ttc-departures"

  // ---------------------------------------------------------------- settings
  readonly property int stopId: parseInt(setting("stopId", 0), 10) || 0
  readonly property string stopName: String(setting("stopName", ""))
  readonly property string route: String(setting("route", "")).trim()
  readonly property string stopWords: String(setting("stop", "")).trim()
  readonly property string routes: String(setting("routes", "")).trim()
  readonly property int maxShown: Math.max(1, parseInt(setting("maxShown", 2), 10) || 2)
  readonly property int refreshSeconds: Math.max(15, parseInt(setting("refreshSeconds", 30), 10) || 30)
  readonly property string labelStyle: String(setting("labelStyle", "Minutes"))
  readonly property string planFrom: String(setting("planFrom", ""))
  readonly property string planTo: String(setting("planTo", ""))

  // A stop given as route + words (the 0.2 way) is resolved once through the
  // helper's search and then behaves like a stop number.
  property int resolvedStopId: 0
  property string resolvedName: ""
  property string resolveError: ""
  readonly property bool needsLookup: stopId <= 0 && route !== "" && stopWords !== ""
  readonly property int activeStopId: stopId > 0 ? stopId : resolvedStopId
  readonly property string routeFilter: routes !== "" ? routes : (stopId <= 0 && route !== "" ? route : "")

  // ---------------------------------------------------------------- hub data
  readonly property string subscriptionKey: activeStopId > 0 ? Model.subscriptionKey(activeStopId, routeFilter, 12) : ""
  property int hubToken: 0
  property string hubKey: ""
  property var data: Model.emptyData()
  property bool fetching: false
  property double clockTick: Date.now()

  function resubscribe() {
    if (hubKey !== "" && hubToken > 0) Hub.unsubscribe(hubKey, hubToken)
    hubKey = ""
    hubToken = 0
    if (subscriptionKey === "") { data = Model.emptyData(); return }
    hubKey = subscriptionKey
    var args = [String(activeStopId), "--routes", routeFilter, "--max", "12"]
    hubToken = Hub.subscribe(hubKey, args, refreshSeconds)
    var e = Hub.entry(hubKey)
    if (e) { data = e.data; fetching = e.fetching }
  }

  Connections {
    target: Hub
    function onUpdated(key) {
      if (key !== root.hubKey) return
      var e = Hub.entry(key)
      if (e) root.data = e.data
    }
    function onFetchingChanged(key) {
      if (key !== root.hubKey) return
      var e = Hub.entry(key)
      root.fetching = e ? e.fetching : false
    }
    function onSearchResults(token, results, error) {
      if (token !== root.lookupToken) return
      root.lookupToken = 0
      var pick = null
      for (var i = 0; i < results.length; i++) {
        if (results[i].routes && results[i].routes.indexOf(root.route) !== -1) { pick = results[i]; break }
      }
      if (!pick && results.length && root.route === "") pick = results[0]
      if (pick) {
        root.resolvedStopId = parseInt(pick.code, 10) || 0
        root.resolvedName = pick.name
        root.resolveError = ""
      } else {
        root.resolveError = error || ("no stop on route " + root.route + " matches \u201c" + root.stopWords + "\u201d")
      }
    }
  }

  property int lookupToken: 0
  function lookup() {
    if (!needsLookup) return
    resolveError = ""
    lookupToken = Hub.search(stopWords + " " + route)
  }

  onSubscriptionKeyChanged: resubscribe()
  onRefreshSecondsChanged: if (hubKey !== "") Hub.updateInterval(hubKey, hubToken, refreshSeconds)
  onNeedsLookupChanged: { resolvedStopId = 0; resolvedName = ""; lookup() }
  onRouteChanged: if (needsLookup) { resolvedStopId = 0; lookup() }
  onStopWordsChanged: if (needsLookup) { resolvedStopId = 0; lookup() }
  Component.onCompleted: { if (needsLookup) lookup(); resubscribe() }
  Component.onDestruction: if (hubKey !== "" && hubToken > 0) Hub.unsubscribe(hubKey, hubToken)

  Timer { interval: 15000; running: true; repeat: true; onTriggered: root.clockTick = Date.now() }

  function refresh() {
    if (needsLookup && resolvedStopId === 0) lookup()
    if (hubKey !== "") Hub.requestNow(hubKey)
  }

  // ---------------------------------------------------------------- persistence
  // Settings live inline in this widget's shell.json entry. The facade only
  // grants updateEntryInline for the plugin's own id, and it replaces the
  // whole entry, so every existing key is copied first. Applied locally
  // before the write so the UI moves on the click, not on the reload.
  function persist(changes) {
    var entry = { id: root.moduleName }
    for (var k in root.settings) if (k !== "id") entry[k] = root.settings[k]
    for (var c in changes) {
      if (changes[c] === null || changes[c] === undefined) delete entry[c]
      else entry[c] = changes[c]
    }
    root.settings = entry
    var shell = root.bar ? root.bar.shell : null
    if (shell && typeof shell.updateEntryInline === "function") shell.updateEntryInline(root.moduleName, entry)
  }

  function chooseStop(stop) {
    if (!stop || !stop.code) return
    persist({ stopId: parseInt(stop.code, 10), stopName: stop.name, route: null, stop: null })
  }

  function persistPlan(fromCode, toCode) {
    persist({ planFrom: fromCode ? String(fromCode) : null, planTo: toCode ? String(toCode) : null })
  }

  // ---------------------------------------------------------------- label
  readonly property string glyph: {
    var stop = data && data.stop ? data.stop : null
    if (stop) return Model.stopGlyph(stop, Hub.routesTable)
    return Model.glyphFor(Model.kindOf(route || (routes.split(",")[0] || ""), Hub.routesTable))
  }
  readonly property string displayName: (data && data.stop && data.stop.name) || resolvedName || stopName
  readonly property string label: {
    clockTick
    if (activeStopId <= 0) {
      if (needsLookup) return glyph + (resolveError !== "" ? " ?" : " \u2026")
      return glyph + " set stop"
    }
    if (!data || (!data.ok && !data.error)) return glyph + " \u2026"
    if (!data.ok) return glyph + " !"
    var text = Model.barLabel(data, maxShown, labelStyle, clockTick)
    if (data.alerts && data.alerts.length) text += " " + Model.GLYPH.alert
    return glyph + " " + text
  }
  readonly property string tooltip: {
    clockTick
    if (activeStopId <= 0) {
      if (resolveError !== "") return "TTC Departures: " + resolveError
      if (needsLookup) return "TTC Departures: finding \u201c" + stopWords + "\u201d on route " + route + "\u2026"
      return "TTC Departures: click to choose a stop"
    }
    if (!data || (!data.ok && !data.error)) return "TTC Departures: loading\u2026"
    return Model.boardText(data, clockTick)
  }

  // ---------------------------------------------------------------- panel
  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
  }

  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false
  readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing === true : false
  function open() { if (panelLoader.item && panelLoader.item.openFromHotkey) panelLoader.item.openFromHotkey() }
  function close() { if (panelLoader.item && panelLoader.item.close) panelLoader.item.close() }
  function closeForPopoutSwitch() { if (panelLoader.item) panelLoader.item.closeForPopoutSwitch() }
  function togglePanel() { if (panelLoader.item && panelLoader.item.toggle) panelLoader.item.toggle() }

  function notifyBoard() {
    if (!root.bar || typeof root.bar.run !== "function") return
    var text = root.tooltip.replace(/\\/g, "\\\\").replace(/"/g, "\\\"").replace(/\$/g, "\\$").replace(/`/g, "\\`")
    root.bar.run("omarchy-notification-send \"" + text + "\"")
  }

  visible: true
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: { root.injectPanel(); Qt.callLater(root.injectPanel) }
  }

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.label
    tooltipText: root.opened ? "" : root.tooltip
    onPressed: function (b) {
      if (b === Qt.MiddleButton) root.refresh()
      else if (b === Qt.RightButton) root.notifyBoard()
      else root.togglePanel()
    }
  }
}
