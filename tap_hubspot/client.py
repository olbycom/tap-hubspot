"""REST client handling, including HubspotStream base class."""

from __future__ import annotations

import datetime
import sys
from typing import Any, Callable

import requests
from singer_sdk import typing as th
from singer_sdk._singerlib.utils import strptime_to_utc
from singer_sdk.pagination import BaseAPIPaginator
from singer_sdk.streams import RESTStream

if sys.version_info >= (3, 8):
    from functools import cached_property
else:
    from cached_property import cached_property

from singer_sdk.authenticators import BearerTokenAuthenticator
from singer_sdk.streams.core import REPLICATION_INCREMENTAL
from tap_hubspot.auth import HubSpotOAuthAuthenticator

if sys.version_info < (3, 11):
    from backports.datetime_fromisoformat import MonkeyPatch

    MonkeyPatch.patch_fromisoformat()

_Auth = Callable[[requests.PreparedRequest], requests.PreparedRequest]


class HubspotStream(RESTStream):
    """tap-hubspot stream class."""

    @property
    def url_base(self) -> str:
        """
        Returns base url
        """
        return "https://api.hubapi.com/"

    records_jsonpath = "$[*]"  # Or override `parse_response`.

    # Set this value or override `get_new_paginator`.
    next_page_token_jsonpath = "$.next_page"

    @cached_property
    def authenticator(self) -> _Auth:
        """Return a new authenticator object.

        Returns:
            An authenticator instance.
        """

        if "refresh_token" in self.config:
            return HubSpotOAuthAuthenticator(
                self,
                auth_endpoint="https://api.hubapi.com/oauth/v1/token",
            )
        else:
            return BearerTokenAuthenticator(
                self,
                token=self.config.get("access_token"),
            )

    @property
    def http_headers(self) -> dict:
        """Return the http headers needed.

        Returns:
            A dictionary of HTTP headers.
        """
        headers = {}
        if "user_agent" in self.config:
            headers["User-Agent"] = self.config.get("user_agent")
        return headers

    def get_new_paginator(self) -> BaseAPIPaginator:
        """Create a new pagination helper instance.

        If the source API can make use of the `next_page_token_jsonpath`
        attribute, or it contains a `X-Next-Page` header in the response
        then you can remove this method.

        If you need custom pagination that uses page numbers, "next" links, or
        other approaches, please read the guide: https://sdk.meltano.com/en/v0.25.0/guides/pagination-classes.html.

        Returns:
            A pagination helper instance.
        """
        return super().get_new_paginator()

    def get_next_page_token(
        self,
        response: requests.Response,
        previous_token: t.Any | None,
    ) -> t.Any | None:
        """Return a token for identifying next page or None if no more pages."""
        # If pagination is required, return a token which can be used to get the
        #       next page. If this is the final page, return "None" to end the
        #       pagination loop.
        resp_json = response.json()
        paging = resp_json.get("paging")

        if paging is not None:
            next_page_token = resp_json.get("paging", {}).get("next", {}).get("after")
        else:
            next_page_token = None
        return next_page_token

    def get_url_params(
        self,
        context: dict | None,
        next_page_token: Any | None,
    ) -> dict[str, Any]:
        """Return a dictionary of values to be used in URL parameterization.

        Args:
            context: The stream context.
            next_page_token: The next page index or value.

        Returns:
            A dictionary of URL query parameters.
        """
        params: dict = {}
        params["limit"] = 100
        if next_page_token:
            params["after"] = next_page_token
        if self.replication_key:
            params["sort"] = "asc"
            params["order_by"] = self.replication_key
        return params


class DynamicHubspotStream(HubspotStream):
    """DynamicHubspotStream"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def _get_datatype(self, data_type: str) -> th.JSONTypeHelper:
        # TODO: consider typing more precisely
        return th.StringType()

    @cached_property
    def schema(self) -> dict:
        """Return a draft JSON schema for this stream."""
        hs_props = []
        self.hs_properties = self._get_available_properties()
        for name, type in self.hs_properties.items():
            hs_props.append(th.Property(name, self._get_datatype(type)))
        schema = th.PropertiesList(
            th.Property("id", th.StringType),
            th.Property(
                "properties",
                th.ObjectType(*hs_props),
            ),
            th.Property("createdAt", th.DateTimeType),
            th.Property("updatedAt", th.DateTimeType),
            th.Property("archived", th.BooleanType),
        )
        return schema.to_dict()

    def _get_available_properties(self) -> dict[str, str]:
        session = requests.Session()
        session.auth = self.authenticator

        resp = session.get(
            f"https://api.hubapi.com/crm/v3/properties/{self.name}",
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return {prop["name"]: prop["type"] for prop in results}

    def get_url_params(
        self,
        context: dict | None,
        next_page_token: Any | None,
    ) -> dict[str, Any]:
        """Return a dictionary of values to be used in URL parameterization.

        Args:
            context: The stream context.
            next_page_token: The next page index or value.

        Returns:
            A dictionary of URL query parameters.
        """
        params = super().get_url_params(context, next_page_token)
        if self.hs_properties:
            params["properties"] = ",".join(self.hs_properties)
        return params


class DynamicIncrementalHubspotStream(DynamicHubspotStream):
    """DynamicIncrementalHubspotStream"""

    date_filter = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def _is_incremental_search(self, context):
        return (
            self.replication_method == REPLICATION_INCREMENTAL
            and self.get_starting_replication_key_value(context)
            and hasattr(self, "incremental_path")
            and self.incremental_path
        )

    @cached_property
    def schema(self) -> dict:
        """Return a draft JSON schema for this stream."""
        hs_props = []
        self.hs_properties = self._get_available_properties()
        for name, type in self.hs_properties.items():
            hs_props.append(th.Property(name, self._get_datatype(type)))
        schema = th.PropertiesList(
            th.Property("id", th.StringType),
            th.Property(
                "properties",
                th.ObjectType(*hs_props),
            ),
            th.Property("createdAt", th.DateTimeType),
            th.Property("updatedAt", th.DateTimeType),
            th.Property("archived", th.BooleanType),
        )
        if self.replication_key:
            schema.append(
                th.Property(
                    self.replication_key,
                    th.DateTimeType,
                )
            )
        return schema.to_dict()

    def get_url_params(
        self,
        context: dict | None,
        next_page_token: Any | None,
    ) -> dict[str, Any]:
        """Return a dictionary of values to be used in URL parameterization.

        Args:
            context: The stream context.
            next_page_token: The next page index or value.

        Returns:
            A dictionary of URL query parameters.
        """
        if self._is_incremental_search(context):
            return {}
        return super().get_url_params(context, next_page_token)

    def post_process(
        self,
        row: dict,
        context: dict | None = None,  # noqa: ARG002
    ) -> dict | None:
        """As needed, append or transform raw data to match expected structure.
        Optional. This method gives developers an opportunity to "clean up" the results
        prior to returning records to the downstream tap - for instance: cleaning,
        renaming, or appending properties to the raw record result returned from the
        API.
        Developers may also return `None` from this method to filter out
        invalid or not-applicable records from the stream.
        Args:
            row: Individual record in the stream.
            context: Stream partition or context dictionary.
        Returns:
            The resulting record dict, or `None` if the record should be excluded.
        """
        if self.replication_key:
            val = None
            if props := row.get("properties"):
                val = props[self.replication_key]
            row[self.replication_key] = val
        return row

    def prepare_request(
        self,
        context: dict | None,
        next_page_token: _TToken | None,
    ) -> requests.PreparedRequest:
        if self._is_incremental_search(context):
            # Search endpoints use POST request
            self.path = self.incremental_path
            self.rest_method = "POST"
        return super().prepare_request(context, next_page_token)

    def prepare_request_payload(
        self,
        context: dict | None,
        next_page_token: _TToken | None,
    ) -> dict | None:
        """Prepare the data payload for the REST API request.

        By default, no payload will be sent (return None).

        Developers may override this method if the API requires a custom payload along
        with the request. (This is generally not required for APIs which use the
        HTTP 'GET' method.)

        Args:
            context: Stream partition or context dictionary.
            next_page_token: Token, page number or any request argument to request the
                next page of data.
        """
        body = {}
        if self._is_incremental_search(context):
            # Only filter in case we have a value to filter on
            # https://developers.hubspot.com/docs/api/crm/search
            if self.date_filter is None:
                self.date_filter = datetime.datetime.fromisoformat(self.get_starting_replication_key_value(context))

            if next_page_token:
                # Hubspot wont return more than 10k records so when we hit 10k we
                # need to reset our epoch to most recent and not send the next_page_token
                if int(next_page_token) + 100 >= 10000:
                    next_date_filter = strptime_to_utc(
                        self.get_context_state(context).get("progress_markers").get("replication_key_value")
                    )
                    if self.date_filter == next_date_filter:
                        # TODO: Temporary workaround, this has to be fixed the proper way otherwise data will be missing
                        self.logger.warning(
                            "More than 10k objects in the search result have the same lastmodifieddate. Adding 1 second in the next iteration date filter to avoid getting stuck in an infinite loop."
                        )
                        self.date_filter = self.date_filter + datetime.timedelta(seconds=1)
                    else:
                        self.date_filter = next_date_filter
                    self.logger.warning(
                        f"Date filter set to {self.date_filter.isoformat()} based on progress marker value."
                    )
                else:
                    body["after"] = next_page_token
            epoch_ts = str(int(self.date_filter.timestamp() * 1000))

            body.update(
                {
                    "filterGroups": [
                        {
                            "filters": [
                                {
                                    "propertyName": self.replication_key,
                                    "operator": "GTE",
                                    # Timestamps need to be in milliseconds
                                    # https://legacydocs.hubspot.com/docs/faq/how-should-timestamps-be-formatted-for-hubspots-apis
                                    "value": epoch_ts,
                                }
                            ]
                        }
                    ],
                    "sorts": [
                        {
                            # This is inside the properties object
                            "propertyName": self.replication_key,
                            "direction": "ASCENDING",
                        }
                    ],
                    # Hubspot sets a limit of most 200 per request. Default is 10
                    "limit": 100,
                    "properties": list(self.hs_properties),
                }
            )

        return body
