#!/usr/bin/env python3
"""Meshtastic Pirate Weather bot.

Listens for ``weather`` commands on a configured Meshtastic channel and sends
the response directly to the requesting node.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from dotenv import load_dotenv
from pubsub import pub

from meshtastic.tcp_interface import TCPInterface


LOG = logging.getLogger("pirate-weather-bot")
TRIGGER = re.compile(r"^\s*weather(?:\s+(?P<location>.+?))?\s*$", re.IGNORECASE)
WEATHER_API_ATTEMPTS = 3
WEATHER_API_RETRY_DELAY = 2
MAX_MESSAGE_LENGTH = 200
load_dotenv()


@dataclass(frozen=True)
class Config:
    api_key: str
    node_ip: str
    channel_indexes: frozenset[int]
    location_country_codes: str = ""


class ResilientTCPInterface(TCPInterface):
    """Keep the library heartbeat thread alive after a reconnectable write error."""

    def _writeBytes(self, payload: bytes) -> None:
        try:
            super()._writeBytes(payload)
        except BrokenPipeError as exc:
            if getattr(self, "_wantExit", False):
                raise
            LOG.warning(
                "Meshtastic TCP connection broke during a write; reconnecting: %s",
                exc,
            )
            reconnect = getattr(self, "_reconnect", None)
            if not callable(reconnect):
                raise
            reconnect()


class WeatherBot:
    """Fetch and format weather using Pirate Weather and Nominatim."""

    def __init__(self, api_key: str, country_codes: str = "") -> None:
        self.api_key = api_key
        self.country_codes = country_codes

    def geocode_location(self, location: str) -> Optional[Tuple[float, float]]:
        params = {"q": location, "format": "json", "limit": 1}
        if self.country_codes:
            params["countrycodes"] = self.country_codes
        url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "PirateWeatherBot/2.0 (self-hosted Meshtastic bot)"},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                results = json.loads(response.read().decode("utf-8"))
            if results:
                return float(results[0]["lat"]), float(results[0]["lon"])
        except urllib.error.HTTPError as exc:
            LOG.warning("Nominatim returned HTTP %s for %r", exc.code, location)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
            LOG.warning("Could not geocode %r: %s", location, exc)
        return None

    @staticmethod
    def _number(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def get_cardinal_direction(degrees: float) -> str:
        directions = (
            "⬆️N", "↗️NNE", "↗️NE", "↗️ENE",
            "➡️E", "↘️ESE", "↘️SE", "↘️SSE",
            "⬇️S", "↙️SSW", "↙️SW", "↙️WSW",
            "⬅️W", "↖️WNW", "↖️NW", "↖️NNW",
        )
        index = int((degrees / 22.5) + 0.5)
        return directions[index % len(directions)]

    def get_weather(self, location: str) -> str:
        display_location = location.title()
        coordinates = self.geocode_location(location)
        if coordinates is None:
            return (
                f"Could not find {display_location!r}. Try a 5-digit ZIP code or "
                '"City, State" format.'
            )
        if not self.api_key:
            return "Weather service is not configured: PIRATE_WEATHER_API_KEY is missing."

        lat, lon = coordinates
        url = f"https://api.pirateweather.net/forecast/{self.api_key}/{lat},{lon}"
        request = urllib.request.Request(url)
        data = None
        for attempt in range(1, WEATHER_API_ATTEMPTS + 1):
            try:
                with urllib.request.urlopen(request, timeout=15) as response:
                    data = json.loads(response.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                if exc.code not in {408, 429} and exc.code < 500:
                    return f"Weather API error: HTTP {exc.code}."
                error = exc
            except (OSError, json.JSONDecodeError) as exc:
                error = exc

            if attempt < WEATHER_API_ATTEMPTS:
                LOG.warning(
                    "Weather API attempt %s/%s failed: %s; retrying",
                    attempt,
                    WEATHER_API_ATTEMPTS,
                    error,
                )
                time.sleep(WEATHER_API_RETRY_DELAY)
            else:
                LOG.error("Weather API failed after %s attempts: %s", attempt, error)
                if isinstance(error, urllib.error.HTTPError):
                    return f"Weather API error: HTTP {error.code}."
                return f"Weather service unavailable: {error}."

        if not isinstance(data, dict):
            LOG.error("Weather API returned an unexpected response")
            return "Weather service returned an invalid response."

        current = data.get("currently", {})
        if not isinstance(current, dict):
            LOG.error("Weather API response has no valid current conditions")
            return "Weather service returned invalid current conditions."
        temp = self._number(current.get("temperature"))
        feels_like = self._number(
            current.get("feels_like", current.get("apparentTemperature", temp))
        )
        humidity = self._number(current.get("humidity")) * 100
        wind = self._number(current.get("windSpeed"))
        pressure = self._number(current.get("pressure"))
        wind_bearing = self._number(current.get("windBearing"))
        summary = current.get("summary", "No summary available")
        wind_direction = self.get_cardinal_direction(wind_bearing)
        cloud_cover = current.get("cloudCover")
        weather_icon = current.get("icon")
        condition_icons = {
            "thunderstorm": "⛈️",
            "rain": "🌧️",
            "sleet": "🌨️",
            "snow": "🌨️",
            "hail": "🌨️",
        }
        condition_icon = (
            condition_icons.get(weather_icon.strip().lower())
            if isinstance(weather_icon, str)
            else None
        )
        cloud_text = ""
        is_daytime: Optional[bool] = None
        if cloud_cover is not None:
            clouds = self._number(cloud_cover) * 100
            if clouds == 0:
                cloud_text = ""
            elif clouds < 30:
                cloud_text = f"🌤️ {clouds:.0f}%"
            elif clouds < 70:
                cloud_text = f"⛅ {clouds:.0f}%"
            else:
                cloud_text = f"☁️ {clouds:.0f}%"

        visibility = current.get("visibility")
        visibility_text = (
            f"👁️ {self._number(visibility):.1f}mi\n"
            if visibility is not None
            else ""
        )

        if wind == 0:
            wind_text = "🌬️ Calm"
        elif wind < 4:
            wind_text = f"🍃 Light {wind:.1f}mph"
        elif wind < 13:
            wind_text = f"🌬️ {wind:.1f}mph"
        elif wind < 25:
            wind_text = f"💨 Breezy {wind:.1f}mph"
        else:
            wind_text = f"🌪️ {wind:.1f}mph"

        day_night_icon = "🌦️"
        event_text = ""
        temperature_range_text = ""
        daily = data.get("daily", {})
        daily_forecasts = daily.get("data", []) if isinstance(daily, dict) else []
        if (
            isinstance(daily_forecasts, list)
            and daily_forecasts
            and isinstance(daily_forecasts[0], dict)
        ):
            today = daily_forecasts[0]
            temperature_min = today.get("temperatureMin")
            temperature_max = today.get("temperatureMax")
            if temperature_min is not None and temperature_max is not None:
                try:
                    temperature_min = float(temperature_min)
                    temperature_max = float(temperature_max)
                except (TypeError, ValueError):
                    LOG.warning("Weather API returned invalid daily temperatures")
                else:
                    if math.isfinite(temperature_min) and math.isfinite(temperature_max):
                        temperature_range_text = (
                            f"{temperature_min:.0f}°/{temperature_max:.0f}°F"
                        )
                    else:
                        LOG.warning(
                            "Weather API returned non-finite daily temperatures"
                        )
            sunrise = today.get("sunriseTime")
            sunset = today.get("sunsetTime")
            if sunrise is not None and sunset is not None:
                try:
                    sunrise = float(sunrise)
                    sunset = float(sunset)
                except (TypeError, ValueError):
                    LOG.warning("Weather API returned invalid sunrise/sunset times")
                    sunrise = sunset = None
                else:
                    if not math.isfinite(sunrise) or not math.isfinite(sunset):
                        LOG.warning("Weather API returned non-finite sunrise/sunset times")
                        sunrise = sunset = None
                if sunrise is not None and sunset is not None:
                    now = time.time()
                    is_daytime = sunrise <= now < sunset
                    day_night_icon = "🌞" if is_daytime else "🌜"
                    next_event = sunset if is_daytime else sunrise
                    if not is_daytime and now >= sunset:
                        next_event += 24 * 60 * 60
                    minutes_until_event = max(0, int((next_event - now + 59) // 60))
                    hours, minutes = divmod(minutes_until_event, 60)
                    event_icon = "🌇" if is_daytime else "🌅"
                    event_text = f"{event_icon} {hours}h{minutes:02d}m\n"

        if cloud_cover is not None and clouds == 0:
            cloud_text = "🌙 Serene" if is_daytime is False else "☀️ Sunny"

        if condition_icon:
            cloud_percentage = f"{clouds:.0f}%" if cloud_cover is not None else ""
            cloud_text = " ".join(
                part for part in (condition_icon, cloud_percentage) if part
            )

        condition = " ".join(
            part for part in (cloud_text, str(summary).lower()) if part
        )
        message = (
            f"{day_night_icon} {display_location}:\n"
            f"ⓘ {condition}, feels like {feels_like:.0f}°F\n"
            f"🌡️ {temp:.0f}°F ({temperature_range_text})\n"
            f"💧 {humidity:.0f}%\n"
            f"🗜️ {pressure:.0f}hPa\n"
            f"{visibility_text}"
            f"{event_text}"
            f"{wind_text} {wind_direction}"
        )
        safe_message = message[:MAX_MESSAGE_LENGTH]
        LOG.info("Formatted payload length: %s characters", len(safe_message))
        return safe_message

    @staticmethod
    def help_text() -> str:
        return (
            "🌦️ **Weather** **Bot** 🤖:\n"
            "*Follow the White Rabbit:*\n"
            "**weather Tampa**\n"
            "**weather 33610**\n"
            "**weather help** see how deep the rabbit hole goes."
            )

    def respond(self, message: str) -> Optional[str]:
        match = TRIGGER.match(message)
        if not match:
            return None
        location = (match.group("location") or "").strip().strip("\"'")
        if not location or location.lower() in {"help", "h", "?"}:
            return self.help_text() if location else "Usage: weather <location>."
        return self.get_weather(location)


def load_config(args: argparse.Namespace) -> Config:
    api_key = os.environ.get("PIRATE_WEATHER_API_KEY", "").strip()
    node_ip = args.ip or os.environ.get("MESH_NODE_IP", "192.168.50.46").strip()
    channel_value = args.channels or args.channel
    if channel_value is None:
        channel_value = os.environ.get(
            "MESH_CHANNEL_INDEXES",
            os.environ.get("MESH_CHANNEL_INDEX", "5"),
        )
    try:
        channel_indexes = frozenset(int(value.strip()) for value in channel_value.split(",") if value.strip())
    except ValueError as exc:
        raise ValueError("channel indexes must be comma-separated integers") from exc
    if not channel_indexes or not channel_indexes.issubset(range(8)):
        raise ValueError("channel indexes must be between 0 and 7")
    return Config(
        api_key=api_key,
        node_ip=node_ip,
        channel_indexes=channel_indexes,
        location_country_codes=os.environ.get("LOCATION_COUNTRY_CODES", "").strip(),
    )


class MeshWeatherBot:
    """Connect to a Meshtastic node and answer weather commands."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.weather = WeatherBot(config.api_key, config.location_country_codes)
        self.interface: Optional[TCPInterface] = None

    def start(self) -> None:
        LOG.info(
            "Connecting to Meshtastic node %s (channels %s)",
            self.config.node_ip,
            ",".join(str(channel) for channel in sorted(self.config.channel_indexes)),
        )
        self.interface = ResilientTCPInterface(self.config.node_ip)
        pub.subscribe(self.on_receive, "meshtastic.receive")
        LOG.info("Listening for weather commands on configured channels")

    def stop(self, *_: Any) -> None:
        pub.unsubscribe(self.on_receive, "meshtastic.receive")
        if self.interface is not None:
            self.interface.close()
            self.interface = None

    def on_receive(self, packet: Dict[str, Any], interface: Any) -> None:
        mesh_interface = interface or self.interface
        if mesh_interface is None:
            raise RuntimeError("Meshtastic interface is not connected")
        my_info = getattr(mesh_interface, "myInfo", None)
        my_node_id = getattr(my_info, "my_node_num", None)
        if my_node_id is None:
            raise RuntimeError("Connected Meshtastic node ID is unavailable")

        destination = packet.get("to")
        is_direct_message = destination == my_node_id
        is_broadcast = destination in {None, "^all", 0xFFFFFFFF}
        LOG.debug(
            "Received packet: channel=%r portnum=%r from=%r to=%r direct=%s",
            packet.get("channel"),
            packet.get("decoded", {}).get("portnum"),
            packet.get("from"),
            destination,
            is_direct_message,
        )
        channel_index = packet.get("channel", 0)
        if not is_direct_message and (
            channel_index not in self.config.channel_indexes or not is_broadcast
        ):
            return
        decoded = packet.get("decoded", {})
        if decoded.get("portnum") not in {"TEXT_APP", "TEXT_MESSAGE_APP"}:
            return
        message = decoded.get("text")
        sender = packet.get("from")
        if not isinstance(message, str) or not sender:
            return
        response = self.weather.respond(message)
        if response is None:
            return
        LOG.info("Responding to %s for command %r", sender, message)
        mesh_interface.sendText(
            response,
            destinationId=sender,
            channelIndex=channel_index,
        )

    def run_forever(self) -> None:
        self.start()
        try:
            while True:
                time.sleep(1)
        finally:
            self.stop()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ip", help="Meshtastic node IP; overrides MESH_NODE_IP")
    parser.add_argument(
        "--node",
        choices=("morpheus", "nebuchadnezzar"),
        help="select the configured node (default: morpheus)",
    )
    parser.add_argument(
        "--channels",
        help="comma-separated channel indexes (default: MESH_CHANNEL_INDEXES or 5)",
    )
    parser.add_argument(
        "--channel",
        help="deprecated alias for --channels",
    )
    parser.add_argument(
        "--location",
        help="process one weather command and print the response, without Meshtastic",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
        filename="/scripts/logs/pwb.log",
        encoding="utf-8",
    )
    args = parse_args()
    if args.node:
        args.ip = os.environ.get(
            "MESH_NODE_IP_SECONDARY" if args.node == "nebuchadnezzar" else "MESH_NODE_IP",
            "192.168.50.49" if args.node == "nebuchadnezzar" else "192.168.50.46",
        )
    try:
        config = load_config(args)
        bot = MeshWeatherBot(config)
        if args.location is not None:
            response = bot.weather.respond(f"weather {args.location}")
            print(response or WeatherBot.help_text())
            return 0
        signal.signal(signal.SIGTERM, bot.stop)
        signal.signal(signal.SIGINT, bot.stop)
        bot.run_forever()
    except (OSError, ValueError, RuntimeError) as exc:
        LOG.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
