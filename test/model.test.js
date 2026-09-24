const test = require("node:test")
const assert = require("node:assert/strict")
const fs = require("node:fs")
const path = require("node:path")
const Model = require("../Model.js")

const fixture = (name) => fs.readFileSync(path.join(__dirname, "fixtures", name), "utf8")

test("parses a stop served by several routes", () => {
  const m = Model.parse(fixture("multi-route.json"))
  assert.equal(m.ok, true)
  assert.equal(m.stopTitle, "The Queensway At Windermere Ave East Side")
  assert.deepEqual(m.routes.map((r) => r.tag), ["501", "301"])
  assert.equal(m.routes[0].directions.length, 1)
  assert.ok(m.routes[0].directions[0].minutes.length >= 1)
  assert.deepEqual(m.routes[1].directions, [], "a route with no vehicles has no directions")
})

test("parses a stop where the feed collapses lists into objects", () => {
  const m = Model.parse(fixture("single-route.json"))
  assert.equal(m.ok, true)
  assert.equal(m.routes.length, 1)
  assert.ok(m.routes[0].directions[0].minutes.length >= 1)
})

test("reports the feed's own error for an unknown stop", () => {
  const m = Model.parse(fixture("invalid-stop.json"))
  assert.equal(m.ok, false)
  assert.match(m.error, /not valid/)
})

test("rejects garbage without throwing", () => {
  assert.equal(Model.parse("<html>").ok, false)
  assert.equal(Model.parse("").ok, false)
  assert.equal(Model.parse("null").ok, false)
})

test("bar label shows the soonest arrivals across routes, soonest first", () => {
  const m = {
    ok: true, error: "", stopTitle: "X",
    routes: [
      { tag: "501", title: "501-Queen", directions: [{ title: "East - 501 Queen towards Neville Park", minutes: [12, 22] }] },
      { tag: "72", title: "72-Pape", directions: [{ title: "South - 72 Pape towards Union", minutes: [0, 9] }] }
    ]
  }
  assert.equal(Model.barLabel(m, 2, "Minutes"), "now·9")
  assert.equal(Model.barLabel(m, 3, "Minutes"), "now·9·12")
  assert.equal(Model.barLabel(m, 2, "Route and minutes"), "72 now·9")
})

test("bar label shows a dash when nothing is coming", () => {
  const m = { ok: true, error: "", stopTitle: "X", routes: [{ tag: "301", title: "301", directions: [] }] }
  assert.equal(Model.barLabel(m, 2, "Minutes"), "—")
})

test("route filter keeps only the listed tags and ignores spacing", () => {
  const m = Model.parse(fixture("multi-route.json"))
  assert.deepEqual(Model.filterRoutes(m, " 301 ").routes.map((r) => r.tag), ["301"])
  assert.deepEqual(Model.filterRoutes(m, "").routes.map((r) => r.tag), ["501", "301"])
  assert.deepEqual(Model.filterRoutes(m, "999").routes, [])
})

test("streetcar routes get the tram glyph, buses get the bus glyph", () => {
  assert.equal(Model.isStreetcar("501"), true)
  assert.equal(Model.isStreetcar("512"), true)
  assert.equal(Model.isStreetcar("304"), true)
  assert.equal(Model.isStreetcar("72"), false)
  assert.equal(Model.isStreetcar("5"), false)
  assert.notEqual(Model.glyphFor("501"), Model.glyphFor("72"))
})

test("board text lists the stop, then each route and direction", () => {
  const text = Model.boardText(Model.parse(fixture("multi-route.json")))
  const lines = text.split("\n")
  assert.equal(lines[0], "The Queensway At Windermere Ave East Side")
  assert.match(lines[1], /^501 East to Neville Park: \d+(, \d+)* min$/)
  assert.equal(lines[2], "301: no vehicles scheduled")
})

test("urls target the ttc agency with the stop id encoded", () => {
  assert.equal(Model.predictionsUrl(14282), "https://retro.umoiq.com/service/publicJSONFeed?command=predictions&a=ttc&stopId=14282")
  assert.equal(Model.mapUrl("14 282"), "https://retro.umoiq.com/googleMap/index.jsp?a=ttc&stopId=14%20282")
})
