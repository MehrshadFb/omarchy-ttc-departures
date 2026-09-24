// TTC real-time predictions come from the UmoIQ (formerly NextBus) public
// JSON feed. It needs no API key. The feed collapses single-item lists into
// bare objects, so every list access goes through asList().
//
// This file is plain JavaScript so it loads both as a QML import and under
// Node for the unit tests in test/.

var FEED = "https://retro.umoiq.com/service/publicJSONFeed"
var GLYPH_BUS = String.fromCodePoint(0xF00E7)   // nf-md-bus
var GLYPH_TRAM = String.fromCodePoint(0xF052D)  // nf-md-tram

function predictionsUrl(stopId) {
  return FEED + "?command=predictions&a=ttc&stopId=" + encodeURIComponent(String(stopId))
}

function mapUrl(stopId) {
  return "https://retro.umoiq.com/googleMap/index.jsp?a=ttc&stopId=" + encodeURIComponent(String(stopId))
}

function asList(value) {
  if (value === undefined || value === null) return []
  return Array.isArray(value) ? value : [value]
}

function empty(error) {
  return { ok: false, error: error || "", stopTitle: "", routes: [] }
}

// Streetcars run on the 501-512 day routes and the 301-312 night routes.
// Everything else on the feed is a bus. Subway lines are not in this feed.
function isStreetcar(tag) {
  return /^(5(0[1-9]|1[0-2])|3(0[1-9]|1[0-2]))$/.test(String(tag))
}

function glyphFor(tag) {
  return isStreetcar(tag) ? GLYPH_TRAM : GLYPH_BUS
}

// "East - 501 Queen towards Neville Park" -> "East to Neville Park"
function shortDirection(title) {
  var text = String(title || "")
  var match = text.match(/^\s*(\w+)\s*-\s*.*?\btowards\s+(.+?)\s*$/i)
  if (match) return match[1] + " to " + match[2]
  return text
}

function parse(raw) {
  var data
  try {
    data = JSON.parse(String(raw || ""))
  } catch (e) {
    return empty("bad response")
  }
  if (!data || typeof data !== "object") return empty("bad response")
  if (data.Error) return empty(String(data.Error.content || "feed error"))

  var stopTitle = ""
  var routes = []
  var blocks = asList(data.predictions)
  for (var i = 0; i < blocks.length; i++) {
    var block = blocks[i]
    if (!block || !block.routeTag) continue
    if (!stopTitle && block.stopTitle) stopTitle = String(block.stopTitle)
    var directions = []
    var dirs = asList(block.direction)
    for (var j = 0; j < dirs.length; j++) {
      var minutes = []
      var preds = asList(dirs[j].prediction)
      for (var k = 0; k < preds.length; k++) {
        var m = parseInt(preds[k].minutes, 10)
        if (!isNaN(m)) minutes.push(m)
      }
      minutes.sort(function (a, b) { return a - b })
      directions.push({ title: String(dirs[j].title || ""), minutes: minutes })
    }
    routes.push({
      tag: String(block.routeTag),
      title: String(block.routeTitle || block.routeTag),
      directions: directions
    })
  }
  if (routes.length === 0) return empty("no routes at this stop")
  return { ok: true, error: "", stopTitle: stopTitle, routes: routes }
}

// "501, 301" -> only those route tags. Blank keeps every route.
function filterRoutes(model, filter) {
  var wanted = String(filter || "").split(",").map(function (s) { return s.trim() }).filter(Boolean)
  if (!model || !model.ok || wanted.length === 0) return model
  var kept = model.routes.filter(function (r) { return wanted.indexOf(r.tag) !== -1 })
  return { ok: true, error: "", stopTitle: model.stopTitle, routes: kept }
}

// Every upcoming arrival at the stop, soonest first, tagged with its route.
function arrivals(model) {
  var out = []
  if (!model || !model.ok) return out
  for (var i = 0; i < model.routes.length; i++) {
    var route = model.routes[i]
    for (var j = 0; j < route.directions.length; j++) {
      var dir = route.directions[j]
      for (var k = 0; k < dir.minutes.length; k++) {
        out.push({ tag: route.tag, minutes: dir.minutes[k], direction: dir.title })
      }
    }
  }
  out.sort(function (a, b) { return a.minutes - b.minutes })
  return out
}

function minutesText(m) {
  return m <= 0 ? "now" : String(m)
}

// The text shown in the bar. `style` is "Minutes" or "Route and minutes".
function barLabel(model, maxShown, style) {
  if (!model || !model.ok) return ""
  var next = arrivals(model).slice(0, Math.max(1, maxShown || 2))
  if (next.length === 0) return "—"
  var text = next.map(function (a) { return minutesText(a.minutes) }).join("·")
  if (style === "Route and minutes") text = next[0].tag + " " + text
  return text
}

// Multi-line board used by the tooltip and the notification.
function boardText(model) {
  if (!model) return ""
  if (!model.ok) return model.error ? "TTC: " + model.error : "TTC: no data yet"
  var lines = [model.stopTitle || "TTC stop"]
  for (var i = 0; i < model.routes.length; i++) {
    var route = model.routes[i]
    if (route.directions.length === 0) {
      lines.push(route.tag + ": no vehicles scheduled")
      continue
    }
    for (var j = 0; j < route.directions.length; j++) {
      var dir = route.directions[j]
      var mins = dir.minutes.length
        ? dir.minutes.map(minutesText).join(", ") + " min"
        : "no vehicles"
      lines.push(route.tag + " " + shortDirection(dir.title) + ": " + mins)
    }
  }
  return lines.join("\n")
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    predictionsUrl: predictionsUrl, mapUrl: mapUrl, parse: parse, filterRoutes: filterRoutes,
    arrivals: arrivals, barLabel: barLabel, boardText: boardText, glyphFor: glyphFor,
    isStreetcar: isStreetcar, shortDirection: shortDirection
  }
}
