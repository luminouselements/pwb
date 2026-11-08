# Pirate Weather Meshtastic Bot

This is a standalone Linux bot. It connects to a Meshtastic node over TCP,
listens for broadcasts on configured channel indexes, and also accepts direct
messages. Weather responses are sent privately to the node that issued the
command.

## Install

```sh
cd PirateWeatherBot
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Set `PIRATE_WEATHER_API_KEY` in `.env`. The bot loads that file automatically;
do not commit `.env`.

By default, broadcasts are processed on channel index `5`. Configure one or
more indexes with `MESH_CHANNEL_INDEXES` (valid indexes are `0` through `7`).
For ambiguous place names, optionally set `LOCATION_COUNTRY_CODES` to an
ISO 3166-1 alpha-2 country code such as `us`; this limits location lookup to
that country.

## Run

```sh
python pwb.py
```

Application logs are appended to `/scripts/logs/pwb.log`.

Morpheus (`192.168.50.46`) is the default node. Select the optional secondary
node (`192.168.50.49`) with:

```sh
python pwb.py --node nebuchadnezzar
```

You can also choose any node address directly:

```sh
python pwb.py --ip 192.168.50.46
```

## Commands

The bot processes text messages that match one of these commands. Matching is
case-insensitive, and leading or trailing whitespace is ignored:

| Message | Result |
| --- | --- |
| `weather` | Shows `Usage: weather <location>.` |
| `weather help`, `weather h`, or `weather ?` | Shows the help text |
| `weather <location>` | Looks up the location and returns its weather |

For example, `WEATHER Tampa` works, but `what's the weather in Tampa?` does not:
the message must start with the `weather` trigger. Use a place name or ZIP/postal
code immediately after the trigger; formats such as `weather Tampa, Florida` or
`weather 33610` work. Put locations with spaces in quotes when using the
command-line option.

If a location cannot be found, try a 5-digit ZIP code or a more specific
`City, State` name. Messages that do not start with `weather` are ignored.

On Meshtastic, broadcasts are processed only on the configured channel indexes.
Direct messages to the bot are also accepted, regardless of channel. In either
case, the response is sent privately to the node that sent the command.

Examples to send over Meshtastic:

```text
weather Tampa
weather "Tampa, Fl"
weather 33610
weather help
```

Use the command-line option to try a location without connecting to a node:

```sh
python pwb.py --location "Portland, Maine"
```

Locations are resolved with OpenStreetMap's Nominatim service, then current
conditions are fetched from Pirate Weather. The response includes temperature
and feels-like temperature, humidity, pressure, wind speed and direction, and
visibility when provided. Cloud cover or a weather-condition icon and summary
are included where available. Today's low/high and a countdown to sunrise or
sunset are included when the API provides the required daily forecast data.
Measurements are formatted in °F, mph, miles, and hPa. The response may be
shortened to fit the 200-character Meshtastic message limit.

Thunderstorms, rain, sleet, snow, and hail use condition-specific icons.
Other conditions retain the cloud-cover icon. The low and high temperatures
come from Pirate Weather's daily forecast.

Example response:

```text
🌞 Tampa:
ⓘ ⛈️ 85% thunderstorm with light rain, feels like 86°F
🌡️ 83°F (76°/88°F)
💧 78%
🗜️ 1012hPa
👁️ 6.2mi
🌇 0h17m
🌬️ 8.0mph ➡️E
```

Weather API requests are retried up to three times when a network error, rate
limit, or server error occurs. Responses are capped at 200 characters for
Meshtastic delivery.

Broadcast messages received on the configured channels are processed. Set
multiple channels with a comma-separated list:

```ini
MESH_CHANNEL_INDEXES=2,5
```

The response to a broadcast command is sent privately to the requesting node.
`--channels 5,6` can be used for a one-time override. The old
`MESH_CHANNEL_INDEX` and `--channel` settings remain supported for a single
channel.
