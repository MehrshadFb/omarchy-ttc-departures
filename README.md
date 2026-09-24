# TTC Departures for Omarchy

Next TTC bus and streetcar arrivals for one Toronto stop, right in the Omarchy bar.

The widget polls the TTC's public real-time feed (UmoIQ, formerly NextBus) and shows the minutes until the next vehicles. No API key, no account, no extra packages beyond `curl`, which Omarchy already ships.

```
󰔭 5·15        streetcar stop, next two arrivals in minutes
󰃧 501 now·9   bus stop with "Route and minutes" label style
```

- **Hover** for the full board: every route and direction at the stop with upcoming minutes.
- **Click** to open the stop on the live TTC vehicle map in your browser.
- **Right-click** to send the board as a desktop notification.
- **Middle-click** to refresh now, re-running the stop lookup.

## Install

```
omarchy plugin add https://github.com/MehrshadFb/omarchy-ttc-departures.git --enable
```

Then place it on the bar and tell it which stop you wait at:

```
omarchy bar put io.github.mehrshadfb.ttc-departures right
omarchy bar set io.github.mehrshadfb.ttc-departures route 501
omarchy bar set io.github.mehrshadfb.ttc-departures stop "Windermere east"
```

That is it. The widget looks up the route's stop list, picks the stop whose name matches your words, and starts showing arrivals. Hover to confirm it picked the right one: the tooltip's first line is the full stop name.

The same settings are in the widget's settings panel in the bar.

## Choosing the stop

Give the **route number** and **a few words of the stop name**, the way it reads on the TTC sign: usually the cross street. Add `east`, `west`, `north`, or `south` to pick the side of the road for the direction you travel.

| You wait at | `route` | `stop` |
|---|---|---|
| 501 Queen eastbound at Windermere | `501` | `Windermere east` |
| 504 King westbound at Bathurst | `504` | `Bathurst west` |
| 72 Pape southbound at Danforth | `72` | `Danforth south` |

If two stops match, the first one along the route wins, so add the side of the road when it matters. If nothing matches, the bar shows `?` and the tooltip says so.

Power users can set `stopId` to the five-digit stop number printed on the pole instead. It overrides `route` and `stop`.

The feed covers **buses and streetcars**. Subway lines are not in this feed.

## Settings

| Key | Default | Meaning |
|---|---|---|
| `route` | `""` | Route number to look the stop up on, for example `501`. |
| `stop` | `""` | A few words of the stop name, for example `Windermere east`. |
| `stopId` | `0` | Optional five-digit stop number. Overrides `route` and `stop`. |
| `routes` | `""` | Comma-separated routes to show, for example `501,301`. Blank shows the chosen route, or every route when using `stopId`. |
| `maxShown` | `2` | How many upcoming arrivals appear in the bar label. |
| `refreshSeconds` | `30` | Poll interval. Minimum 15. |
| `labelStyle` | `Minutes` | `Minutes` shows `5·15`. `Route and minutes` shows `501 5·15`. |

Example `shell.json` layout entry:

```json
{ "id": "io.github.mehrshadfb.ttc-departures", "settings": { "route": "501", "stop": "Windermere east", "maxShown": 3 } }
```

You can add the widget more than once for different stops.

## Remove

```
omarchy plugin remove io.github.mehrshadfb.ttc-departures
```

## Development

`Model.js` holds all feed parsing and label logic and runs under Node as well as QML. Tests use real feed responses saved in `test/fixtures`.

```
npm test
omarchy plugin validate .
```

## Data

Predictions are provided by the Toronto Transit Commission through its public UmoIQ feed. All data is copyright Toronto Transit Commission. This project is not affiliated with the TTC.

## License

MIT
