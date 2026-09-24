import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model

// Next TTC arrivals for one stop, straight in the bar. Polls the keyless
// UmoIQ predictions feed with curl and shows the soonest vehicles in minutes.
BarWidget {
  id: root
  moduleName: "io.github.mehrshadfb.ttc-departures"

  readonly property int stopId: parseInt(setting("stopId", 0), 10) || 0
  readonly property string routes: String(setting("routes", ""))
  readonly property int maxShown: parseInt(setting("maxShown", 2), 10) || 2
  readonly property int refreshSeconds: Math.max(15, parseInt(setting("refreshSeconds", 30), 10) || 30)
  readonly property string labelStyle: String(setting("labelStyle", "Minutes"))

  property var model: ({ ok: false, error: "", stopTitle: "", routes: [] })
  property bool fetched: false

  readonly property var shownModel: Model.filterRoutes(model, routes)
  readonly property string glyph: shownModel.ok && shownModel.routes.length > 0
    ? Model.glyphFor(shownModel.routes[0].tag)
    : Model.glyphFor("")
  readonly property string label: {
    if (stopId <= 0) return glyph + " set stop"
    if (!fetched) return glyph + " …"
    if (!shownModel.ok) return glyph + " !"
    return glyph + " " + Model.barLabel(shownModel, maxShown, labelStyle)
  }
  readonly property string tooltip: {
    if (stopId <= 0) return "TTC Departures: set a stop id in the widget settings"
    if (!fetched) return "TTC Departures: loading…"
    return Model.boardText(shownModel)
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  function refresh() {
    if (stopId <= 0 || fetchProc.running) return
    fetchProc.running = true
  }

  function apply(raw) {
    model = Model.parse(raw)
    fetched = true
  }

  function openMap() {
    if (stopId <= 0) return
    Quickshell.execDetached(["omarchy-launch-browser", Model.mapUrl(stopId)])
  }

  function notifyBoard() {
    if (!root.bar) return
    var text = Model.boardText(shownModel).replace(/"/g, "\\\"")
    root.bar.run("omarchy-notification-send \"" + text + "\"")
  }

  onStopIdChanged: { fetched = false; refresh() }

  Process {
    id: fetchProc
    command: ["curl", "-fsS", "--max-time", "8", Model.predictionsUrl(root.stopId)]
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
    running: root.stopId > 0
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
      if (b === Qt.MiddleButton) root.refresh()
      else if (b === Qt.RightButton) root.notifyBoard()
      else root.openMap()
    }
  }
}
