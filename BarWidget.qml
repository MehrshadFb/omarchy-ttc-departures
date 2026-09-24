import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model

// Next TTC arrivals for one stop, straight in the bar. Polls the keyless
// UmoIQ predictions feed with curl and shows the soonest vehicles in minutes.
// The stop comes either from a stop number or from a route plus a few words
// of the stop name, resolved once through the route's config.
BarWidget {
  id: root
  moduleName: "io.github.mehrshadfb.ttc-departures"

  readonly property int stopId: parseInt(setting("stopId", 0), 10) || 0
  readonly property string route: String(setting("route", "")).trim()
  readonly property string stop: String(setting("stop", "")).trim()
  readonly property string routes: String(setting("routes", ""))
  readonly property int maxShown: parseInt(setting("maxShown", 2), 10) || 2
  readonly property int refreshSeconds: Math.max(15, parseInt(setting("refreshSeconds", 30), 10) || 30)
  readonly property string labelStyle: String(setting("labelStyle", "Minutes"))

  // Stop resolved from route + stop words. Cleared whenever those change.
  property int resolvedStopId: 0
  property string resolvedTitle: ""
  property string resolveError: ""
  readonly property bool needsLookup: stopId <= 0 && route !== "" && stop !== ""
  readonly property int activeStopId: stopId > 0 ? stopId : resolvedStopId

  property var model: ({ ok: false, error: "", stopTitle: "", routes: [] })
  property bool fetched: false

  // With a route chosen by name, only that route's arrivals are shown unless
  // the routes filter says otherwise.
  readonly property string routeFilter: routes !== "" ? routes : (stopId <= 0 ? route : "")
  readonly property var shownModel: Model.filterRoutes(model, routeFilter)
  readonly property string glyph: Model.glyphFor(shownModel.ok && shownModel.routes.length > 0 ? shownModel.routes[0].tag : route)
  readonly property string label: {
    if (activeStopId <= 0) {
      if (needsLookup) return glyph + (resolveError !== "" ? " ?" : " …")
      return glyph + " set stop"
    }
    if (!fetched) return glyph + " …"
    if (!shownModel.ok) return glyph + " !"
    return glyph + " " + Model.barLabel(shownModel, maxShown, labelStyle)
  }
  readonly property string tooltip: {
    if (activeStopId <= 0) {
      if (resolveError !== "") return "TTC Departures: " + resolveError
      if (needsLookup) return "TTC Departures: finding stop “" + stop + "” on route " + route + "…"
      return "TTC Departures: set a stop number, or a route and stop name, in the widget settings"
    }
    if (!fetched) return "TTC Departures: loading…"
    return Model.boardText(shownModel)
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  function resolve() {
    if (!needsLookup || resolveProc.running) return
    resolveProc.running = true
  }

  function applyConfig(raw) {
    var config = Model.parseRouteConfig(raw)
    if (!config.ok) {
      resolveError = config.error === "no such route" ? "route " + route + " not found" : config.error
      return
    }
    var match = Model.findStop(config, stop)
    if (!match) {
      resolveError = "no stop matching “" + stop + "” on route " + route
      return
    }
    resolveError = ""
    resolvedTitle = match.title
    resolvedStopId = parseInt(match.stopId, 10) || 0
    fetched = false
    refresh()
  }

  function refresh() {
    if (activeStopId <= 0) { resolve(); return }
    if (fetchProc.running) return
    fetchProc.running = true
  }

  function apply(raw) {
    model = Model.parse(raw)
    fetched = true
  }

  function openMap() {
    if (activeStopId <= 0) return
    Quickshell.execDetached(["omarchy-launch-browser", Model.mapUrl(activeStopId)])
  }

  function notifyBoard() {
    if (!root.bar) return
    var text = tooltip.replace(/"/g, "\\\"")
    root.bar.run("omarchy-notification-send \"" + text + "\"")
  }

  function reset() {
    resolvedStopId = 0
    resolvedTitle = ""
    resolveError = ""
    fetched = false
    refresh()
  }

  onStopIdChanged: reset()
  onRouteChanged: reset()
  onStopChanged: reset()

  Process {
    id: resolveProc
    command: ["curl", "-fsS", "--max-time", "10", Model.routeConfigUrl(root.route)]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.applyConfig(text)
    }
    onExited: function (exitCode) {
      if (exitCode !== 0) root.resolveError = "feed unreachable"
    }
  }

  Process {
    id: fetchProc
    command: ["curl", "-fsS", "--max-time", "8", Model.predictionsUrl(root.activeStopId)]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.apply(text)
    }
    onExited: function (exitCode) {
      if (exitCode !== 0) {
        root.model = { ok: false, error: "feed unreachable", stopTitle: "", routes: [] }
        root.fetched = true
      }
    }
  }

  Timer {
    interval: root.refreshSeconds * 1000
    running: root.stopId > 0 || root.needsLookup
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.label
    tooltipText: root.tooltip
    onPressed: function (b) {
      if (b === Qt.MiddleButton) root.reset()
      else if (b === Qt.RightButton) root.notifyBoard()
      else root.openMap()
    }
  }
}
