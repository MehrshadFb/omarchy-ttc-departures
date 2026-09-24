const test = require("node:test")
const assert = require("node:assert/strict")
const Model = require("../Model.js")

const NOW = 1_790_266_000_000 // ms

function data(overrides) {
  return Object.assign({
    ok: true, error: "", source: "bustime", fetched: NOW / 1000 - 20,
    stop: { code: "14282", name: "The Queensway at Windermere Ave East Side", kind: "stop", routes: ["301", "501"] },
    arrivals: [
      { route: "501", time: NOW / 1000 + 250, minutes: 4, towards: "Neville Park", direction: "East", shortTurn: false },
      { route: "501", time: NOW / 1000 + 900, minutes: 15, towards: "Roncesvalles", direction: "East", shortTurn: true },
      { route: "301", time: NOW / 1000 + 1500, minutes: 25, towards: "Neville Park", direction: "East", shortTurn: false }
    ],
    byRoute: [
      { route: "501", kind: "streetcar", direction: "East", towards: "Neville Park", minutes: [4, 15], times: [NOW / 1000 + 250, NOW / 1000 + 900], shortTurns: [false, true] },
      { route: "301", kind: "streetcar", direction: "East", towards: "Neville Park", minutes: [25], times: [NOW / 1000 + 1500], shortTurns: [false] }
    ],
    alerts: [{ effect: "DETOUR", header: "Streetcars divert via King" }]
  }, overrides || {})
}

const ROUTES = { "501": { type: 0 }, "1": { type: 1 }, "72": { type: 3 } }

test("glyphs follow the route kind and platforms are subway", () => {
  assert.equal(Model.kindOf("501", ROUTES), "streetcar")
  assert.equal(Model.kindOf("1", ROUTES), "subway")
  assert.equal(Model.kindOf("72", ROUTES), "bus")
  assert.equal(Model.kindOf("504", {}), "streetcar", "falls back to the 5xx rule without a table")
  assert.equal(Model.stopGlyph({ kind: "platform", routes: ["1"] }, ROUTES), Model.GLYPH.subway)
  assert.equal(Model.stopGlyph({ kind: "stop", routes: ["72"] }, ROUTES), Model.GLYPH.bus)
  assert.notEqual(Model.GLYPH.streetcar, Model.GLYPH.bus)
})

test("bar label recomputes minutes from absolute times", () => {
  const d = data()
  assert.equal(Model.barLabel(d, 2, "Minutes", NOW), "4·15")
  assert.equal(Model.barLabel(d, 3, "Minutes", NOW + 5 * 60000), "now·10·20")
  assert.equal(Model.barLabel(d, 2, "Route and minutes", NOW), "501 4·15")
  assert.equal(Model.barLabel(data({ arrivals: [] }), 2, "Minutes", NOW), "—")
  assert.equal(Model.barLabel({ ok: false }, 2, "Minutes", NOW), "")
})

test("board text lists the stop, each route with direction, alerts, and legacy marker", () => {
  const lines = Model.boardText(data(), NOW).split("\n")
  assert.equal(lines[0], "The Queensway at Windermere Ave East Side")
  assert.equal(lines[1], "501 East to Neville Park: 4, 15* min")
  assert.equal(lines[2], "301 East to Neville Park: 25 min")
  assert.equal(lines[3], "Detour: Streetcars divert via King")
  assert.match(Model.boardText(data({ source: "nextbus" }), NOW), /legacy feed/)
  assert.equal(Model.boardText(data({ byRoute: [], alerts: [] }), NOW).split("\n")[1], "No vehicles predicted")
  assert.equal(Model.boardText({ ok: false, error: "feed unreachable" }, NOW), "TTC: feed unreachable")
})

test("formatting helpers", () => {
  assert.equal(Model.minutesText(0), "now")
  assert.equal(Model.minutesText(7), "7")
  assert.equal(Model.durationText(1260), "21 min")
  assert.equal(Model.durationText(3600), "1 h ")
  assert.equal(Model.ageText(NOW / 1000 - 30, NOW), "30s ago")
  assert.equal(Model.ageText(NOW / 1000 - 300, NOW), "5 min ago")
  assert.equal(Model.effectText("NO_SERVICE"), "No service")
  assert.equal(Model.effectText("whatever"), "Notice")
  assert.equal(Model.directionText({ direction: "East", towards: "Neville Park" }), "East to Neville Park")
  assert.equal(Model.directionText({ towards: "Finch" }), "To Finch")
  assert.equal(Model.subscriptionKey(14282, "501, 301", 12), "14282|501,301|12")
})

test("stop title and subtitle read naturally for platforms and surface stops", () => {
  const platform = { code: "13816", kind: "platform", station: "Union", dir: "Northbound", towards: "Finch Station", routes: ["1"], name: "Union Station - Northbound Platform Towards Finch" }
  assert.equal(Model.stopTitle(platform), "Union Station")
  assert.equal(Model.stopSubtitle(platform), "Line 1 · Northbound to Finch · #13816")
  const stop = { code: "14282", kind: "stop", routes: ["301", "501"], name: "The Queensway at Windermere Ave East Side" }
  assert.equal(Model.stopTitle(stop), stop.name)
  assert.equal(Model.stopSubtitle(stop), "301 · 501 · #14282")
  assert.equal(Model.stopSubtitle({ code: "1", kind: "stop", routes: ["1", "2", "3", "4", "5", "6", "7"] }), "1 · 2 · 3 · 4 · 5 · 6 … · #1")
})

test("headsigns are shortened to a destination", () => {
  assert.equal(Model.cleanHeadsign("Line 1 (Yonge-University) towards Finch Station"), "to Finch")
  assert.equal(Model.cleanHeadsign("North - 510 Spadina towards Spadina Station"), "to Spadina")
  assert.equal(Model.cleanHeadsign("East - 501 Queen"), "501 Queen")
  assert.equal(Model.cleanHeadsign(""), "")
})

test("itinerary title and legs text", () => {
  const start = NOW / 1000
  const it = {
    start, end: start + 1260, duration: 1260, transfers: 1,
    legs: [
      { mode: "WALK", transit: false, duration: 240, start, end: start + 240 },
      { mode: "SUBWAY", transit: true, route: "2", kind: "subway", headsign: "Kennedy", from: "Bathurst", to: "St George", start: start + 240, end: start + 420 },
      { mode: "WALK", transit: false, duration: 60 },
      { mode: "SUBWAY", transit: true, route: "1", kind: "subway", headsign: "Finch", from: "St George", to: "Union", start: start + 540, end: start + 1080 }
    ]
  }
  assert.match(Model.itineraryTitle(it), /^\d\d:\d\d → \d\d:\d\d · 21 min · 1 transfer$/)
  const legs = Model.legsText(it).split("\n")
  assert.equal(legs.length, 3, "walks under two minutes are dropped")
  assert.equal(legs[0], "Walk 4 min")
  assert.match(legs[1], /^.+ 2 Kennedy · \d\d:\d\d → St George$/)
  assert.equal(Model.itineraryTitle({ start, end: start + 600, duration: 600, transfers: 0 }).endsWith("direct"), true)
})
