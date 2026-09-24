import QtQuick
import Quickshell
import qs.Commons
import qs.Ui
import "Model.js" as Model

// The dropdown for the TTC Departures bar widget.
//
// Three modes share one card:
//   board  every route at the stop with the next arrivals, plus alerts
//   pick   search the shipped stop list and choose a new stop
//   plan   pick From and To stops and get itineraries from Transitous
//
// Keys:  j/k or arrows move   Enter select   r refresh   s change stop
//        p plan a trip        x swap From/To  Esc back, then close
Panel {
  id: root
  moduleName: "io.github.mehrshadfb.ttc-departures"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  readonly property var barIdentity: hostWidget || root
  property bool openedFromHotkey: false

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.5)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  readonly property var data: hostWidget && hostWidget.data ? hostWidget.data : Model.emptyData()
  readonly property bool fetching: hostWidget ? hostWidget.fetching === true : false
  readonly property var stop: data && data.stop ? data.stop : null
  readonly property var rows: data && data.byRoute ? data.byRoute : []
  readonly property var alerts: data && data.alerts ? data.alerts.slice(0, 4) : []

  property string mode: "board"
  property int cursor: 0
  property bool cursorActive: false
  property double tick: Date.now()
  Timer { interval: 10000; running: root.opened; repeat: true; onTriggered: root.tick = Date.now() }

  readonly property string freshnessText: {
    tick
    if (fetching) return "Updating…"
    if (!data || !data.fetched) return "No data yet"
    var age = Model.ageText(data.fetched, tick)
    var src = data.source === "nextbus" ? " · legacy feed" : ""
    return "Updated " + age + src
  }

  // ---------------------------------------------------------------- lifecycle
  function open() {
    openedFromHotkey = false
    setCenterHoverRevealSuppressed(false)
    root.controller.show()
    enterBoard()
    if (hostWidget && hostWidget.refresh) hostWidget.refresh()
  }
  function openFromHotkey() {
    openedFromHotkey = true
    root.controller.show()
    enterBoard()
    if (hostWidget && hostWidget.refresh) hostWidget.refresh()
    Qt.callLater(function () { if (root.opened) setCenterHoverRevealSuppressed(true) })
  }
  function close() {
    setCenterHoverRevealSuppressed(false)
    root.controller.hide()
  }
  function toggle() { if (root.opened) root.close(); else root.openFromHotkey() }
  function switchPanel(direction) {
    if (root.bar && typeof root.bar.switchPanelFrom === "function") return root.bar.switchPanelFrom(root.barIdentity, direction)
    return false
  }
  function setCenterHoverRevealSuppressed(value) {
    if (root.bar && typeof root.bar.setCenterHoverRevealSuppressed === "function") root.bar.setCenterHoverRevealSuppressed(value)
  }
  onOpenedChanged: if (!opened) { mode = "board"; cursorActive = false }

  function enterBoard() {
    mode = "board"
    cursor = 0
    cursorActive = false
    Qt.callLater(function () { keyCatcher.forceActiveFocus() })
  }

  // ---------------------------------------------------------------- picking
  property var results: []
  property int searchToken: 0
  property string searchError: ""
  property string pickTarget: "stop"   // "stop", "from", or "to"

  function startPicking(target) {
    pickTarget = target || "stop"
    mode = "pick"
    results = []
    searchError = ""
    cursor = 0
    cursorActive = true
    searchField.text = ""
    Qt.callLater(function () { searchField.forceActiveFocus() })
  }

  function runSearch() {
    var q = searchField.text.trim()
    if (q === "") { results = []; searchToken = 0; return }
    searchToken = Hub.search(q)
  }

  function choose(stop) {
    if (!stop) return
    if (pickTarget === "from") { planFrom = stop; enterPlan() }
    else if (pickTarget === "to") { planTo = stop; enterPlan() }
    else { if (hostWidget && hostWidget.chooseStop) hostWidget.chooseStop(stop); enterBoard() }
  }

  Connections {
    target: Hub
    function onSearchResults(token, list, error) {
      if (token !== root.searchToken) return
      root.results = list
      root.searchError = error
      root.cursor = 0
    }
    function onPlanResults(token, result) {
      if (token !== root.planToken) return
      root.planning = false
      root.planResult = result
      root.cursor = 0
    }
  }

  Timer { id: searchDebounce; interval: 160; onTriggered: root.runSearch() }

  // ---------------------------------------------------------------- planning
  property var planFrom: null
  property var planTo: null
  property var planResult: null
  property bool planning: false
  property int planToken: 0
  readonly property var itineraries: planResult && planResult.itineraries ? planResult.itineraries : []

  function enterPlan() {
    mode = "plan"
    cursor = 0
    cursorActive = true
    if (!planFrom && stop) planFrom = stop
    Qt.callLater(function () { keyCatcher.forceActiveFocus() })
    if (planFrom && planTo) runPlan()
  }

  function runPlan() {
    if (!planFrom || !planTo) return
    if (planFrom.code === planTo.code) { planResult = { ok: false, error: "From and To are the same stop", itineraries: [] }; return }
    planning = true
    planResult = null
    planToken = Hub.plan(planFrom.code, planTo.code)
    if (hostWidget && hostWidget.persistPlan) hostWidget.persistPlan(planFrom.code, planTo.code)
  }

  function swapPlan() {
    var a = planFrom
    planFrom = planTo
    planTo = a
    if (planFrom && planTo) runPlan()
  }

  // Remembered From/To codes come back as names through a search each.
  property int fromLookupToken: 0
  property int toLookupToken: 0
  function restorePlanFromSettings() {
    var from = hostWidget ? hostWidget.planFrom : ""
    var to = hostWidget ? hostWidget.planTo : ""
    if (from && !planFrom) fromLookupToken = Hub.search(from)
    if (to && !planTo) toLookupToken = Hub.search(to)
  }
  Connections {
    target: Hub
    function onSearchResults(token, list, error) {
      if (token === root.fromLookupToken && list.length) { root.planFrom = list[0]; root.fromLookupToken = 0 }
      else if (token === root.toLookupToken && list.length) { root.planTo = list[0]; root.toLookupToken = 0 }
    }
  }
  onHostWidgetChanged: restorePlanFromSettings()

  // ---------------------------------------------------------------- keyboard
  readonly property int listLength: mode === "pick" ? results.length : (mode === "plan" ? 2 + itineraries.length : rows.length)

  function moveCursor(dy) {
    if (dy === 0 || listLength === 0) return
    cursorActive = true
    cursor = Math.max(0, Math.min(listLength - 1, cursor + dy))
  }

  function activate() {
    if (mode === "pick") { if (results.length) choose(results[Math.min(cursor, results.length - 1)]) }
    else if (mode === "plan") {
      if (cursor === 0) startPicking("from")
      else if (cursor === 1) startPicking("to")
    }
  }

  function back() {
    if (mode === "board") root.close()
    else enterBoard()
  }

  // ---------------------------------------------------------------- card
  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    popoutSwitching: root.popoutSwitching
    popoutSwitchClosing: root.popoutSwitchClosing
    focusTarget: root.mode === "pick" ? searchField : keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(400))
    contentHeight: panel.fittedContentHeight(chromeHeight + bodyColumn.implicitHeight, Style.space(620))

    readonly property int bodyTopGap: Style.space(12)
    readonly property int bodyBottomGap: Style.space(10)
    readonly property int footerTopGap: Style.space(10)
    readonly property int chromeHeight: headerBlock.implicitHeight + bodyTopGap + bodyBottomGap
      + footerSeparator.height + footerTopGap + footerBlock.implicitHeight

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      blocked: searchField.activeFocus

      onMoveRequested: function (dx, dy) { root.moveCursor(dy) }
      onActivateRequested: root.activate()
      onReturnRequested: root.activate()
      onCloseRequested: root.back()
      onTabRequested: function (direction) { root.switchPanel(direction) }
      onTextKey: function (t) {
        var key = t.toLowerCase()
        if (key === "r") { if (root.hostWidget && root.hostWidget.refresh) root.hostWidget.refresh() }
        else if (key === "s") root.startPicking("stop")
        else if (key === "p") root.enterPlan()
        else if (key === "b") root.enterBoard()
        else if (key === "x" && root.mode === "plan") root.swapPlan()
        else if (key === "f" && root.mode === "plan") root.startPicking("from")
        else if (key === "t" && root.mode === "plan") root.startPicking("to")
      }

      // ------------------------------------------------------------ header
      Item {
        id: headerBlock
        anchors { top: parent.top; left: parent.left; right: parent.right }
        implicitHeight: hero.implicitHeight

        PanelHero {
          id: hero
          anchors { left: parent.left; right: parent.right }
          foreground: root.foreground
          fontFamily: root.fontFamily
          title: root.mode === "pick" ? (root.pickTarget === "stop" ? "Choose a stop" : (root.pickTarget === "from" ? "Trip from" : "Trip to"))
               : root.mode === "plan" ? "Plan a trip"
               : (root.stop ? root.stop.name : (root.hostWidget && root.hostWidget.displayName ? root.hostWidget.displayName : "TTC Departures"))
          meta: root.mode === "pick" ? "Street, station, or stop number"
              : root.mode === "plan" ? "Routing by Transitous · schedule times"
              : (root.stop ? (root.stop.kind === "platform" ? "Line " + root.stop.routes.join(", ") + " · " + (root.stop.dir || "") : "Routes " + root.stop.routes.join(" · ")) : "")
          detail: root.mode === "board" ? root.freshnessText : ""
          iconComponent: Component {
            Text {
              text: root.mode === "plan" ? Model.GLYPH.pin : (root.stop ? Model.stopGlyph(root.stop, Hub.routesTable) : Model.GLYPH.bus)
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.display
            }
          }
        }
      }

      // ------------------------------------------------------------ body
      Flickable {
        id: body
        anchors { top: headerBlock.bottom; topMargin: panel.bodyTopGap; left: parent.left; right: parent.right; bottom: footerSeparator.top; bottomMargin: panel.bodyBottomGap }
        contentWidth: width
        contentHeight: bodyColumn.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds

        Column {
          id: bodyColumn
          width: body.width
          spacing: Style.space(6)

          // ---- board ----
          Repeater {
            model: root.mode === "board" ? root.rows : []
            CursorSurface {
              id: routeRow
              required property var modelData
              required property int index
              width: bodyColumn.width
              implicitHeight: routeLine.implicitHeight + Style.space(12)
              hasCursor: root.cursorActive && root.cursor === index
              foreground: root.foreground

              Item {
                id: routeLine
                anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; leftMargin: Style.space(10); rightMargin: Style.space(10) }
                implicitHeight: Math.max(routeName.implicitHeight, minutesText.implicitHeight)

                Text {
                  id: routeGlyph
                  anchors.left: parent.left
                  anchors.verticalCenter: parent.verticalCenter
                  text: Model.glyphFor(routeRow.modelData.kind)
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.title
                }
                Text {
                  id: routeName
                  anchors { left: routeGlyph.right; leftMargin: Style.space(8); right: minutesText.left; rightMargin: Style.space(8); verticalCenter: parent.verticalCenter }
                  textFormat: Text.PlainText
                  text: routeRow.modelData.route + "  " + Model.directionText(routeRow.modelData)
                  elide: Text.ElideRight
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                }
                Text {
                  id: minutesText
                  anchors.right: parent.right
                  anchors.verticalCenter: parent.verticalCenter
                  textFormat: Text.PlainText
                  text: {
                    root.tick
                    var out = []
                    var r = routeRow.modelData
                    for (var j = 0; j < Math.min(r.times.length, 4); j++) {
                      var m = Model.minutesText(Model.liveMinutes({ time: r.times[j], minutes: r.minutes[j] }, root.tick))
                      if (r.shortTurns && r.shortTurns[j]) m += "*"
                      out.push(m)
                    }
                    return out.join("  ") + " min"
                  }
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                }
              }
            }
          }

          Text {
            visible: root.mode === "board" && root.rows.length === 0
            width: bodyColumn.width
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            text: !root.stop ? "No stop chosen yet. Press s to search for one."
                : (root.data.error ? root.data.error : "No vehicles predicted right now.")
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
          }

          Text {
            visible: root.mode === "board" && root.rows.some(function (r) { return r.shortTurns && r.shortTurns.some(function (x) { return x }) })
            width: bodyColumn.width
            textFormat: Text.PlainText
            text: "* short turn: this vehicle ends before the terminus"
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }

          PanelSectionHeader {
            visible: root.mode === "board" && root.alerts.length > 0
            text: "SERVICE ALERTS"
            foreground: root.foreground
            fontFamily: root.fontFamily
          }

          Repeater {
            model: root.mode === "board" ? root.alerts : []
            Item {
              required property var modelData
              width: bodyColumn.width
              implicitHeight: alertText.implicitHeight + Style.space(4)
              Text {
                id: alertGlyph
                anchors.left: parent.left
                anchors.top: parent.top
                text: Model.GLYPH.alert
                color: root.urgent
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
              }
              Text {
                id: alertText
                anchors { left: alertGlyph.right; leftMargin: Style.space(8); right: parent.right; top: parent.top }
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
                maximumLineCount: 3
                elide: Text.ElideRight
                text: Model.effectText(parent.modelData.effect) + ": " + parent.modelData.header
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
              }
            }
          }

          // ---- pick ----
          TextField {
            id: searchField
            visible: root.mode === "pick"
            width: bodyColumn.width
            placeholderText: "Queen Bathurst, Union, 14282…"
            foreground: root.foreground
            font.family: root.fontFamily
            onTextChanged: if (root.mode === "pick") searchDebounce.restart()
            Keys.onPressed: function (event) {
              if (event.key === Qt.Key_Down) { root.moveCursor(1); event.accepted = true }
              else if (event.key === Qt.Key_Up) { root.moveCursor(-1); event.accepted = true }
              else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) { root.activate(); event.accepted = true }
              else if (event.key === Qt.Key_Escape) { root.back(); event.accepted = true }
            }
          }

          Repeater {
            model: root.mode === "pick" ? root.results : []
            CursorSurface {
              id: resultRow
              required property var modelData
              required property int index
              width: bodyColumn.width
              implicitHeight: resultName.implicitHeight + resultSub.implicitHeight + Style.space(12)
              hasCursor: root.cursor === index
              foreground: root.foreground

              Text {
                id: resultGlyph
                anchors { left: parent.left; leftMargin: Style.space(10); verticalCenter: parent.verticalCenter }
                text: Model.stopGlyph(resultRow.modelData, Hub.routesTable)
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.title
              }
              Text {
                id: resultName
                anchors { left: resultGlyph.right; leftMargin: Style.space(8); right: parent.right; rightMargin: Style.space(10); top: parent.top; topMargin: Style.space(6) }
                textFormat: Text.PlainText
                text: resultRow.modelData.name
                elide: Text.ElideRight
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
              }
              Text {
                id: resultSub
                anchors { left: resultName.left; right: parent.right; rightMargin: Style.space(10); top: resultName.bottom }
                textFormat: Text.PlainText
                text: Model.stopSubtitle(resultRow.modelData)
                elide: Text.ElideRight
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
              }
              MouseArea {
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onEntered: root.cursor = resultRow.index
                onClicked: { root.cursor = resultRow.index; root.activate() }
              }
            }
          }

          Text {
            visible: root.mode === "pick" && root.results.length === 0 && searchField.text.trim() !== ""
            width: bodyColumn.width
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            text: root.searchError !== "" ? root.searchError : "No stops match. Try the cross street or the stop number."
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
          }

          // ---- plan ----
          Repeater {
            model: root.mode === "plan" ? [{ label: "From", stop: root.planFrom, target: "from" }, { label: "To", stop: root.planTo, target: "to" }] : []
            CursorSurface {
              id: endpointRow
              required property var modelData
              required property int index
              width: bodyColumn.width
              implicitHeight: endpointName.implicitHeight + Style.space(14)
              hasCursor: root.cursorActive && root.cursor === index
              foreground: root.foreground

              Text {
                id: endpointLabel
                anchors { left: parent.left; leftMargin: Style.space(10); verticalCenter: parent.verticalCenter }
                width: Style.space(44)
                textFormat: Text.PlainText
                text: endpointRow.modelData.label
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
              }
              Text {
                id: endpointName
                anchors { left: endpointLabel.right; right: parent.right; rightMargin: Style.space(10); verticalCenter: parent.verticalCenter }
                textFormat: Text.PlainText
                text: endpointRow.modelData.stop ? endpointRow.modelData.stop.name : "Choose a stop…"
                elide: Text.ElideRight
                color: endpointRow.modelData.stop ? root.foreground : root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
              }
              MouseArea {
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onEntered: { root.cursorActive = true; root.cursor = endpointRow.index }
                onClicked: root.startPicking(endpointRow.modelData.target)
              }
            }
          }

          Text {
            visible: root.mode === "plan" && (root.planning || (root.planResult && !root.planResult.ok) || (!root.planFrom || !root.planTo))
            width: bodyColumn.width
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            text: root.planning ? "Finding itineraries…"
                : (!root.planFrom || !root.planTo) ? "Choose both stops. Enter opens the picker, x swaps."
                : (root.planResult ? root.planResult.error : "")
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
          }

          PanelSectionHeader {
            visible: root.mode === "plan" && root.itineraries.length > 0
            text: "ITINERARIES"
            foreground: root.foreground
            fontFamily: root.fontFamily
          }

          Repeater {
            model: root.mode === "plan" ? root.itineraries : []
            CursorSurface {
              id: itinRow
              required property var modelData
              required property int index
              width: bodyColumn.width
              implicitHeight: itinTitle.implicitHeight + itinLegs.implicitHeight + Style.space(14)
              hasCursor: root.cursorActive && root.cursor === index + 2
              foreground: root.foreground

              Text {
                id: itinTitle
                anchors { left: parent.left; right: parent.right; top: parent.top; margins: Style.space(8) }
                textFormat: Text.PlainText
                text: Model.itineraryTitle(itinRow.modelData)
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
              }
              Text {
                id: itinLegs
                anchors { left: parent.left; right: parent.right; top: itinTitle.bottom; leftMargin: Style.space(8); rightMargin: Style.space(8); topMargin: Style.space(2) }
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
                text: Model.legsText(itinRow.modelData)
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
              }
              MouseArea {
                anchors.fill: parent
                hoverEnabled: true
                onEntered: { root.cursorActive = true; root.cursor = itinRow.index + 2 }
              }
            }
          }
        }
      }

      // ------------------------------------------------------------ footer
      PanelSeparator {
        id: footerSeparator
        anchors { left: parent.left; right: parent.right; bottom: footerBlock.top; bottomMargin: panel.footerTopGap }
        foreground: root.foreground
      }

      Item {
        id: footerBlock
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
        implicitHeight: Math.max(hintText.implicitHeight, footerButtons.implicitHeight)

        Text {
          id: hintText
          anchors.left: parent.left
          anchors.verticalCenter: parent.verticalCenter
          textFormat: Text.PlainText
          text: root.mode === "pick" ? "Type to search · Enter select · Esc back"
              : root.mode === "plan" ? "f from · t to · x swap · Esc back"
              : "s stop · p plan · r refresh"
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
        }

        Row {
          id: footerButtons
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          spacing: Style.space(6)

          Button {
            visible: root.mode === "board"
            text: "Change stop"
            bordered: true
            foreground: root.foreground
            fontFamily: root.fontFamily
            onClicked: root.startPicking("stop")
          }
          Button {
            visible: root.mode === "board"
            text: "Plan trip"
            bordered: true
            foreground: root.foreground
            fontFamily: root.fontFamily
            onClicked: root.enterPlan()
          }
          Button {
            visible: root.mode === "plan"
            text: "Swap"
            bordered: true
            foreground: root.foreground
            fontFamily: root.fontFamily
            onClicked: root.swapPlan()
          }
          Button {
            visible: root.mode !== "board"
            text: "Back"
            bordered: true
            foreground: root.foreground
            fontFamily: root.fontFamily
            onClicked: root.enterBoard()
          }
        }
      }
    }
  }
}
