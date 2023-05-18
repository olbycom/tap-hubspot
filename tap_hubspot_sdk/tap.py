"""tap-hubspot-sdk tap class."""

from __future__ import annotations

from singer_sdk import Tap
from singer_sdk import typing as th  # JSON schema typing helpers

# TODO: Import your custom stream types here:
from tap_hubspot_sdk import streams


class TapHubspot(Tap):
    """tap-hubspot-sdk tap class."""

    name = "tap-hubspot-sdk"

    # TODO: Update this section with the actual config values you expect:
    config_jsonschema = th.PropertiesList(
        th.Property(
            "access_token",
            th.StringType,
            required=True,
            description="The token to authenticate against the API service",
        ),
        th.Property(
            "start_date",
            th.DateTimeType,
            description="The earliest record date to sync",
        ),
    ).to_dict()

    def discover_streams(self) -> list[streams.HubspotStream]:
        """Return a list of discovered streams.

        Returns:
            A list of discovered streams.
        """
        return [
            streams.ListsStream(self),
        ]


if __name__ == "__main__":
    TapHubspot.cli()
