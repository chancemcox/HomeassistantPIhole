"""Curated deny-list presets for the Pi-hole Home Assistant UI."""

from __future__ import annotations

from typing import Any

# Hulu/Disney advertising endpoints reported by Pi-hole users.
# Do NOT include vortex.hulu.com or doppler.hulu.com — those break playback reporting.
HULU_ADS_PRESET: dict[str, Any] = {
    "id": "hulu-ads",
    "title": "Hulu ads",
    "description": (
        "Blocks common Hulu / Disney advertising and tracking hostnames. "
        "DNS blocking cannot catch every in-stream ad; some ad breaks may skip a few seconds of video."
    ),
    "comment": "pihole-ha preset: hulu-ads",
    "exact": [
        "ads-e-darwin.hulustream.com",
        "ads-v-darwin.hulustream.com",
        "ads-api.hulu.com",
        "track.hulu.com",
        "logs.hulu.com",
        "collector.hulu.com",
        "assets.huluim.com",
        "ads.hulu.com",
        "t2.hulu.com",
        "vortex-webhooks.hulu.com",
        "us-east-1.ads.digital.disneyadvertising.com",
        "ads.digital.disneyadvertising.com",
        "disneyadvertising.com",
    ],
    "regex": [
        r"(^|\.)disneyadvertising\.com$",
        r"(^|\.)ads-.*\.hulustream\.com$",
        r"(^|\.)ads-api\.hulu\.com$",
    ],
    "never_block": [
        "vortex.hulu.com",
        "doppler.hulu.com",
        "play.hulu.com",
        "www.hulu.com",
        "auth.hulu.com",
    ],
    "notes": [
        "Point the Hulu device at Pi-hole DNS first (router DHCP or device DNS).",
        "Expect imperfect results: Hulu often serves ads from content CDNs.",
        "If playback glitches near ad breaks, remove the disneyadvertising.com regex.",
        "Never block vortex.hulu.com or doppler.hulu.com.",
        "Ad-free Hulu is the only fully reliable option.",
    ],
}


PRESETS: dict[str, dict[str, Any]] = {
    HULU_ADS_PRESET["id"]: HULU_ADS_PRESET,
}


def list_presets() -> list[dict[str, Any]]:
    return [
        {
            "id": preset["id"],
            "title": preset["title"],
            "description": preset["description"],
            "exact_count": len(preset["exact"]),
            "regex_count": len(preset["regex"]),
            "notes": preset["notes"],
            "never_block": preset["never_block"],
        }
        for preset in PRESETS.values()
    ]
