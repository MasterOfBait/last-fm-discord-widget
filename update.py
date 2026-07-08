import json
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

LAST_FM_USERNAME = os.getenv("LAST_FM_USERNAME")
API_KEY = os.getenv("API_KEY")
APPLICATION_ID = os.getenv("APPLICATION_ID")
BOT_TOKEN = os.getenv("BOT_TOKEN")
USER_ID = os.getenv("USER_ID")
LAST_FM_API_URL = "https://ws.audioscrobbler.com/2.0/"
DISCORD_PROFILE_URL = (
    f"https://discord.com/api/v9/applications/{APPLICATION_ID}/users/"
    f"{USER_ID}/identities/0/profile"
)

WEEKLY_LAST_FM_PERIOD = "7day"
WEEKLY_DISPLAY_PERIOD = "this week"
IMAGE_FIELD_NAMES = {"latestscrobbleimg"}
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 2


def last_fm_get(method, **params):
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(
                LAST_FM_API_URL,
                params={
                    "method": method,
                    "user": LAST_FM_USERNAME,
                    "api_key": API_KEY,
                    "format": "json",
                    **params,
                },
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()

            if "error" in data:
                raise ValueError(f"Last.fm API error {data['error']}: {data.get('message', 'Unknown error')}")

            return data
        except (requests.exceptions.RequestException, ValueError) as error:
            last_error = error
            print(f"Last.fm request '{method}' failed on attempt {attempt}/{MAX_RETRIES}: {error}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_SECONDS * attempt)

    raise last_error


def first_item(value):
    if isinstance(value, list):
        return value[0] if value else {}
    return value or {}


def image_url(item):
    images = item.get("image", []) if isinstance(item, dict) else []
    if not isinstance(images, list):
        return ""

    preferred_sizes = ["mega", "extralarge", "large", "medium", "small"]
    images_by_size = {
        image.get("size"): image.get("#text", "")
        for image in images
        if isinstance(image, dict)
    }

    for size in preferred_sizes:
        url = images_by_size.get(size, "")
        if is_valid_image_url(url):
            return url

    for image in reversed(images):
        url = image.get("#text", "") if isinstance(image, dict) else ""
        if is_valid_image_url(url):
            return url

    return ""


def format_number(value):
    return f"{int(value):,}"


def is_valid_image_url(value):
    if not isinstance(value, str):
        return False

    return value.lower().startswith("https://")


def get_user_info():
    user_data = last_fm_get("user.getinfo")
    print(f"Last.fm user.getinfo response: {json.dumps(user_data, indent=2)}")

    user = user_data.get("user", {})
    playcount = user.get("playcount")
    print(f"Extracted user.playcount value: {playcount}")

    return {
        "scrobbles": format_number(playcount or 0),
        "artistscrobbled": format_number(user.get("artist_count", 0)),
    }


def get_recent_scrobble():
    scrobble_data = last_fm_get("user.getrecenttracks", limit=1, extended=1)
    track = first_item(scrobble_data.get("recenttracks", {}).get("track", []))
    artist = track.get("artist", {})
    artist_name = artist.get("name") or artist.get("#text", "")
    track_name = track.get("name", "")
    latest_scrobble_image = image_url(track)

    print(f"latestscrobbleimg Last.fm API response image data: {json.dumps(track.get('image', []), indent=2)}")
    print(f"latestscrobbleimg extracted image URL: {latest_scrobble_image}")
    print(f"latestscrobbleimg image URL empty: {not bool(latest_scrobble_image)}")
    print(f"latestscrobbleimg image URL valid HTTPS: {is_valid_image_url(latest_scrobble_image)}")

    return {
        "latestscrobble": track_name,
        "latestscrobbleimg": latest_scrobble_image,
        "latestartist": artist_name,
    }


def get_period_stats():
    top_tracks = last_fm_get("user.gettoptracks", period=WEEKLY_LAST_FM_PERIOD, limit=1)
    top_artists = last_fm_get("user.gettopartists", period=WEEKLY_LAST_FM_PERIOD, limit=1)

    track = first_item(top_tracks.get("toptracks", {}).get("track", []))
    top_artist = first_item(top_artists.get("topartists", {}).get("artist", []))

    return {
        "timeperiod": WEEKLY_DISPLAY_PERIOD,
        "topsong": track.get("name", ""),
        "topartist": top_artist.get("name", ""),
        "topartistplays": format_number(top_artist.get("playcount", 0)),
    }


def dynamic_field(name, value, field_type=1):
    clean_value = "" if value is None else str(value).strip()
    return {"type": field_type, "name": name, "value": clean_value}


def image_dynamic_field(name, image_url_value):
    clean_value = "" if image_url_value is None else str(image_url_value).strip()
    return {"type": 3, "name": name, "value": {"url": clean_value}}


def valid_dynamic_field(field):
    value = field.get("value")
    if not value:
        return False

    if field.get("name") in IMAGE_FIELD_NAMES:
        if not isinstance(value, dict):
            return False
        return is_valid_image_url(value.get("url"))

    return True


def clean_dynamic_fields(fields):
    return [field for field in fields if valid_dynamic_field(field)]


def dynamic_values(fields):
    return {field.get("name"): field.get("value") for field in fields}


def log_discord_error(response, payload):
    print(f"Discord update failed: {response.status_code} {response.reason}")
    print(f"Discord request payload: {json.dumps(payload, indent=2)}")
    print(f"Discord response body: {response.text}")


def debug_discord_payload(payload):
    values = dynamic_values(payload.get("data", {}).get("dynamic", []))
    image_values = {
        name: value.get("url")
        for name, value in values.items()
        if name in IMAGE_FIELD_NAMES and isinstance(value, dict)
    }
    print(f"Final Discord payload: {json.dumps(payload, indent=2)}")
    print(f"Final Discord dynamic values: {json.dumps(values, indent=2)}")
    print(f"Final Discord image URLs: {json.dumps(image_values, indent=2)}")
    print(f"Final Discord scrobbles value: {values.get('scrobbles')}")


def update():
    user_info = get_user_info()
    recent_scrobble = get_recent_scrobble()
    period_stats = get_period_stats()

    fields = clean_dynamic_fields([
        dynamic_field("scrobbles", user_info["scrobbles"]),
        dynamic_field("latestscrobble", recent_scrobble["latestscrobble"]),
        image_dynamic_field("latestscrobbleimg", recent_scrobble["latestscrobbleimg"]),
        dynamic_field("latestartist", recent_scrobble["latestartist"]),
        dynamic_field("artistscrobbled", user_info["artistscrobbled"]),
        dynamic_field("timeperiod", period_stats["timeperiod"]),
        dynamic_field("topartist", period_stats["topartist"]),
        dynamic_field("topsong", period_stats["topsong"]),
        dynamic_field("topartistplays", period_stats["topartistplays"]),
    ])
    json_string = {"data": {"dynamic": fields}}

    debug_discord_payload(json_string)

    response = requests.patch(
        url=DISCORD_PROFILE_URL,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bot {BOT_TOKEN}",
            "User-Agent": "DiscordBot (https://github.com/discord/discord-api-docs, 1.0.0)",
        },
        json=json_string,
        timeout=30,
    )
    print(f"Discord response body: {response.text}")
    if not response.ok:
        log_discord_error(response, json_string)
        response.raise_for_status()

    print(response)


update()
