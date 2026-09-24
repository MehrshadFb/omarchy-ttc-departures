// Pure presentation logic shared by the bar widget and the panel, and unit
// tested under Node. Everything here works on the JSON documents produced by
// ttc.py; nothing here touches the network.

var GLYPH = {
  bus: String.fromCodePoint(0xF00E7),      // nf-md-bus
  streetcar: String.fromCodePoint(0xF052D), // nf-md-tram
  subway: String.fromCodePoint(0xF06AC),    // nf-md-subway
  alert: String.fromCodePoint(0xF0026),     // nf-md-alert
  clock: String.fromCodePoint(0xF0150),     // nf-md-clock_outline
  pin: String.fromCodePoint(0xF034E)        // nf-md-map_marker
}

function glyphFor(kind) {
  return GLYPH[kind] || GLYPH.bus
}

// Which glyph represents a stop: its first route's kind, subway for platforms.
function stopGlyph(stop, routesTable) {
  if (!stop) return GLYPH.bus
  if (stop.kind === "platform") return GLYPH.subway
  var first = stop.routes && stop.routes.length ? stop.routes[0] : ""
  return glyphFor(kindOf(first, routesTable))
}

// Route kind from the shipped routes table (type 0 streetcar, 1 subway, 3 bus).
function kindOf(route, routesTable) {
  var info = routesTable && routesTable[String(route)]
  if (!info) return /^(5(0[1-9]|1[0-2])|3(0[1-9]|1[0-2]))$/.test(String(route)) ? "streetcar" : "bus"
  return { 0: "streetcar", 1: "subway", 3: "bus" }[info.type] || "bus"
}

function emptyData() {
  return { ok: false, error: "", stop: null, source: "none", fetched: 0, arrivals: [], byRoute: [], alerts: [] }
}

function minutesText(m) {
  m = Number(m)
  if (isNaN(m)) return ""
  return m <= 0 ? "now" : String(m)
}

function clockText(epochSeconds) {
  if (!epochSeconds) return ""
  var d = new Date(epochSeconds * 1000)
  var h = d.getHours()
  var m = d.getMinutes()
  return (h < 10 ? "0" : "") + h + ":" + (m < 10 ? "0" : "") + m
}

function durationText(seconds) {
  var m = Math.round(Number(seconds || 0) / 60)
  if (m < 60) return m + " min"
  return Math.floor(m / 60) + " h " + (m % 60 ? (m % 60) + " min" : "")
}

function ageText(fetchedEpoch, nowMs) {
  if (!fetchedEpoch) return ""
  var s = Math.max(0, Math.round((nowMs - fetchedEpoch * 1000) / 1000))
  return s < 60 ? s + "s ago" : Math.round(s / 60) + " min ago"
}

// Arrival minutes drift as the clock runs between polls; recompute from the
// absolute times so the label stays honest even when a fetch is late.
function liveMinutes(arrival, nowMs) {
  if (arrival && arrival.time) return Math.max(0, Math.floor((arrival.time * 1000 - nowMs) / 60000))
  return arrival ? Number(arrival.minutes) || 0 : 0
}

// The text shown in the bar next to the glyph.
// On a vertical bar each value takes its own line (separator "\n"), the way
// the first-party clock stacks its digits.
function barLabel(data, maxShown, style, nowMs, separator) {
  if (!data || !data.ok) return ""
  var sep = separator || "·"
  var next = (data.arrivals || []).slice(0, Math.max(1, maxShown || 2))
  if (next.length === 0) return "—"
  var text = next.map(function (a) { return minutesText(liveMinutes(a, nowMs)) }).join(sep)
  if (style === "Route and minutes") text = next[0].route + (sep === "\n" ? "\n" : " ") + text
  return text
}

function effectText(effect) {
  return {
    NO_SERVICE: "No service", REDUCED_SERVICE: "Reduced service", SIGNIFICANT_DELAYS: "Delays",
    DETOUR: "Detour", ADDITIONAL_SERVICE: "Extra service", MODIFIED_SERVICE: "Service change",
    OTHER_EFFECT: "Notice", UNKNOWN_EFFECT: "Notice", STOP_MOVED: "Stop moved", NO_EFFECT: "Notice",
    ACCESSIBILITY_ISSUE: "Accessibility"
  }[effect] || "Notice"
}

function directionText(row) {
  if (!row) return ""
  var parts = []
  if (row.direction) parts.push(row.direction)
  if (row.towards) parts.push((row.direction ? "to " : "To ") + row.towards)
  return parts.join(" ")
}

// Multi-line board for the tooltip and the notification.
function boardText(data, nowMs) {
  if (!data) return ""
  if (!data.ok) return data.error ? "TTC: " + data.error : "TTC: no data yet"
  var stop = data.stop || {}
  var lines = [stop.name || "TTC stop"]
  var rows = data.byRoute || []
  if (rows.length === 0) lines.push(data.source === "none" && data.error ? data.error : "No vehicles predicted")
  for (var i = 0; i < rows.length; i++) {
    var r = rows[i]
    var mins = []
    for (var j = 0; j < r.times.length; j++) {
      var m = minutesText(liveMinutes({ time: r.times[j], minutes: r.minutes[j] }, nowMs))
      if (r.shortTurns && r.shortTurns[j]) m += "*"
      mins.push(m)
    }
    lines.push(r.route + " " + directionText(r) + ": " + mins.join(", ") + " min")
  }
  var alerts = data.alerts || []
  for (var k = 0; k < Math.min(alerts.length, 3); k++) {
    lines.push(effectText(alerts[k].effect) + ": " + alerts[k].header)
  }
  if (data.source === "nextbus") lines.push("(legacy feed)")
  return lines.join("\n")
}

// A stop's headline: surface stops keep their sign name; platforms read as
// the station, with the line and direction on the subtitle line.
function stopTitle(stop) {
  if (!stop) return ""
  if (stop.kind === "platform" && stop.station) return stop.station + " Station"
  return stop.name || ""
}

// "Line 1 · Northbound to Finch · #13816" or "301 · 501 · 508 · #14282".
function stopSubtitle(stop) {
  if (!stop) return ""
  var bits = []
  if (stop.kind === "platform") {
    if (stop.routes && stop.routes.length) bits.push("Line " + stop.routes.join(", "))
    var dir = stop.dir || "Platform"
    if (stop.towards) dir += " to " + stop.towards.replace(/\s+Station$/i, "")
    bits.push(dir)
  } else if (stop.routes && stop.routes.length) {
    bits.push(stop.routes.slice(0, 6).join(" · ") + (stop.routes.length > 6 ? " …" : ""))
  }
  bits.push("#" + stop.code)
  return bits.join(" · ")
}

// "Line 1 (Yonge-University) towards Finch Station" -> "to Finch"
function cleanHeadsign(headsign) {
  var text = String(headsign || "").trim()
  var m = text.match(/towards\s+(.+?)\s*$/i)
  if (m) return "to " + m[1].replace(/\s+(Station|Stn|Loop)$/i, "")
  return text.replace(/^\s*(North|South|East|West)\s*-\s*/i, "")
}

// Settings that identify what the widget shows. Used as the hub cache key so
// two widgets with identical settings share one fetch.
function subscriptionKey(stopCode, routesFilter, maxShown) {
  return String(stopCode || "") + "|" + String(routesFilter || "").replace(/\s+/g, "") + "|" + String(maxShown || 12)
}

// One itinerary as a compact line: "17:04 → 17:21 · 17 min · 1 transfer"
function itineraryTitle(it) {
  if (!it) return ""
  var t = it.transfers || 0
  return clockText(it.start) + " → " + clockText(it.end) + " · " + durationText(it.duration)
    + " · " + (t === 0 ? "direct" : t + (t === 1 ? " transfer" : " transfers"))
}

// The legs of an itinerary as text: "Walk 4 min → 2 Bathurst→St George → 1 → Union"
function legsText(it) {
  if (!it || !it.legs) return ""
  var parts = []
  for (var i = 0; i < it.legs.length; i++) {
    var l = it.legs[i]
    if (!l.transit) {
      if ((l.duration || 0) >= 120) parts.push("Walk " + durationText(l.duration))
      continue
    }
    var head = l.route ? l.route : l.mode
    var to = l.to ? " → " + l.to.replace(/\s+-\s+(North|South|East|West)bound Platform.*$/i, "") : ""
    var sign = l.headsign ? " " + cleanHeadsign(l.headsign) : ""
    parts.push(glyphFor(l.kind) + " " + head + sign + " · " + clockText(l.start) + to)
  }
  return parts.join("\n")
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    GLYPH: GLYPH, glyphFor: glyphFor, stopGlyph: stopGlyph, kindOf: kindOf, emptyData: emptyData,
    minutesText: minutesText, clockText: clockText, durationText: durationText, ageText: ageText,
    liveMinutes: liveMinutes, barLabel: barLabel, effectText: effectText, directionText: directionText,
    boardText: boardText, stopTitle: stopTitle, stopSubtitle: stopSubtitle, cleanHeadsign: cleanHeadsign, subscriptionKey: subscriptionKey,
    itineraryTitle: itineraryTitle, legsText: legsText
  }
}
