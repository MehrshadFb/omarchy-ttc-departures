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
- **Middle-click** to refresh now.

## Install

```
omarchy plugin add https://github.com/MehrshadFb/omarchy-ttc-departures.git --enable
```

Then place it on the bar and set your stop:

```
omarchy bar put io.github.mehrshadfb.ttc-departures right
```

Open the bar settings for the widget (or edit `~/.config/omarchy/shell.json`) and set `stopId`.

## Finding your stop id

Every TTC stop has a five-digit **stop number**. It is printed on the stop pole and on the shelter sign, and it is the number you would text to the TTC's next-vehicle service. You can also open any route on the TTC site or the live map and read the stop number from the stop's details.

The feed covers **buses and streetcars**. Subway lines are not in this feed.

## Settings

| Key | Default | Meaning |
|---|---|---|
| `stopId` | `0` | The five-digit TTC stop number. Required. |
| `routes` | `""` | Comma-separated route numbers to show, for example `501,301`. Blank shows every route at the stop. |
| `maxShown` | `2` | How many upcoming arrivals appear in the bar label. |
| `refreshSeconds` | `30` | Poll interval. Minimum 15. |
| `labelStyle` | `Minutes` | `Minutes` shows `5·15`. `Route and minutes` shows `501 5·15`. |

Example `shell.json` layout entry:

```json
{ "id": "io.github.mehrshadfb.ttc-departures", "settings": { "stopId": 14282, "routes": "501", "maxShown": 3 } }
```

You can add the widget more than once with different stops.

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
