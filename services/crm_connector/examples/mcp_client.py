"""Minimal MCP client for the crm-connector stdio server.

Agent-side sample: connects to ``app.mcp.server`` over stdio using the
official ``mcp`` client and drives the read-only tools end to end.

Usage (run from ``services/crm_connector``):

    python examples/mcp_client.py status
    python examples/mcp_client.py whoami
    python examples/mcp_client.py search --keyword 万象城 [--page 1] [--page-size 3]
    python examples/mcp_client.py detail --listing-id <id>
    python examples/mcp_client.py prospect --listing-id <id>  # 实勘照片记录
    python examples/mcp_client.py demo            # tools -> status -> whoami -> search -> detail

The server subprocess inherits this process's environment, so enable the
real profile by exporting ``CC_UPSTREAM_PROFILE=kecom-prod`` (plus
``CC_CREDENTIAL_STORE_PATH`` if the store lives elsewhere). This file
contains no credentials; failures (auth required, rate limited, upstream
contract changes) print one line on stderr and exit with code 1.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import TextContent

SERVICE_DIR = Path(__file__).resolve().parents[1]


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


async def _call_json(
    session: ClientSession, name: str, arguments: dict[str, Any]
) -> Any | None:
    """Call a tool; return parsed JSON, or None after printing the error."""
    result = await session.call_tool(name, arguments)
    content = result.content[0]
    if result.is_error:
        message = content.text if isinstance(content, TextContent) else "unknown error"
        sys.stderr.write(f"tool {name} failed: {message}\n")
        return None
    assert isinstance(content, TextContent)
    return json.loads(content.text)


async def _run(args: argparse.Namespace) -> bool:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp.server"],
        cwd=str(SERVICE_DIR),
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            if args.command == "demo":
                tools = await session.list_tools()
                _print_json(sorted(tool.name for tool in tools.tools))
                for name, arguments in (
                    ("crm_connection_status", {}),
                    ("crm_whoami", {}),
                    (
                        "rental_listing_search",
                        {
                            "input": {
                                "community_keyword": args.keyword,
                                "page": args.page,
                                "page_size": args.page_size,
                            }
                        },
                    ),
                ):
                    payload = await _call_json(session, name, arguments)
                    if payload is None:
                        return False
                    _print_json(payload)
                assert payload is not None  # loop above never returns with payload None
                search = payload["items"]
                if search:
                    listing = await _call_json(
                        session,
                        "rental_listing_get_detail",
                        {"input": {"listing_id": search[0]["listing_id"]}},
                    )
                    if listing is None:
                        return False
                    _print_json(listing)
                return True

            if args.command == "status":
                payload = await _call_json(session, "crm_connection_status", {})
            elif args.command == "whoami":
                payload = await _call_json(session, "crm_whoami", {})
            elif args.command == "search":
                payload = await _call_json(
                    session,
                    "rental_listing_search",
                    {
                        "input": {
                            "community_keyword": args.keyword,
                            "page": args.page,
                            "page_size": args.page_size,
                        }
                    },
                )
            elif args.command == "prospect":
                payload = await _call_json(
                    session,
                    "rental_listing_get_prospect",
                    {"input": {"listing_id": args.listing_id}},
                )
            elif args.command == "sale-search":
                payload = await _call_json(
                    session,
                    "sale_listing_search",
                    {
                        "input": {
                            "scope": args.scope,
                            "total_price_wan": (
                                {"min": args.price_min, "max": args.price_max}
                                if args.price_min is not None or args.price_max is not None
                                else None
                            ),
                            "rooms": args.rooms,
                            "page": args.page,
                        }
                    },
                )
            elif args.command == "sale-suggest":
                payload = await _call_json(
                    session,
                    "sale_community_suggest",
                    {"input": {"query": args.keyword}},
                )
            elif args.command == "sale-detail":
                payload = await _call_json(
                    session,
                    "sale_listing_get_detail",
                    {"input": {"listing_id": args.listing_id}},
                )
            elif args.command == "sale-maintain":
                payload = await _call_json(
                    session,
                    "sale_listing_get_maintain_info",
                    {"input": {"listing_id": args.listing_id}},
                )
            elif args.command == "sale-follows":
                payload = await _call_json(
                    session,
                    "sale_listing_get_follows",
                    {"input": {"listing_id": args.listing_id}},
                )
            elif args.command == "sale-map-suggest":
                payload = await _call_json(
                    session,
                    "sale_map_suggest",
                    {"input": {"query": args.keyword}},
                )
            elif args.command == "sale-nearby":
                payload = await _call_json(
                    session,
                    "sale_map_nearby_search",
                    {
                        "input": {
                            "location": args.keyword,
                            "radius_meters": args.radius,
                            "total_price_wan": (
                                {"min": args.price_min, "max": args.price_max}
                                if args.price_min is not None or args.price_max is not None
                                else None
                            ),
                            "rooms": args.rooms,
                        }
                    },
                )
            else:  # detail
                payload = await _call_json(
                    session,
                    "rental_listing_get_detail",
                    {"input": {"listing_id": args.listing_id}},
                )
            if payload is None:
                return False
            _print_json(payload)
            return True


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Drive the crm-connector MCP server over stdio.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="connection/authorization status")
    sub.add_parser("whoami", help="CRM principal bound to the connector")

    search = sub.add_parser("search", help="search rental listings")
    search.add_argument("--keyword", default="万象城")
    search.add_argument("--page", type=int, default=1)
    search.add_argument("--page-size", type=int, default=3)

    detail = sub.add_parser("detail", help="fetch one rental listing")
    detail.add_argument("--listing-id", required=True)

    prospect = sub.add_parser("prospect", help="fetch one listing's 实勘 photo record")
    prospect.add_argument("--listing-id", required=True)

    sale_search = sub.add_parser("sale-search", help="search 在售 (买卖) listings")
    sale_search.add_argument("--keyword", default="")
    sale_search.add_argument("--scope", default="gdiv_mt")
    sale_search.add_argument("--price-min", type=int, default=None)
    sale_search.add_argument("--price-max", type=int, default=None)
    sale_search.add_argument("--rooms", type=int, nargs="*", default=[])
    sale_search.add_argument("--page", type=int, default=1)

    sale_suggest = sub.add_parser(
        "sale-suggest", help="resolve a 买卖 community name"
    )
    sale_suggest.add_argument("--keyword", default="成发紫东阳光")

    sale_detail = sub.add_parser(
        "sale-detail", help="fetch one 在售 listing (search row)"
    )
    sale_detail.add_argument("--listing-id", required=True)

    sale_maintain = sub.add_parser(
        "sale-maintain", help="fetch one 在售 listing's 维护信息"
    )
    sale_maintain.add_argument("--listing-id", required=True)

    sale_follows = sub.add_parser(
        "sale-follows", help="fetch one 在售 listing's 跟进记录"
    )
    sale_follows.add_argument("--listing-id", required=True)

    sale_map_suggest = sub.add_parser(
        "sale-map-suggest", help="resolve a 买卖 map phrase (mall/landmark/community)"
    )
    sale_map_suggest.add_argument("--keyword", default="万象城")

    sale_nearby = sub.add_parser(
        "sale-nearby", help="search 在售 listings near a named place"
    )
    sale_nearby.add_argument("--keyword", default="万象城")
    sale_nearby.add_argument("--radius", type=int, default=1000)
    sale_nearby.add_argument("--price-min", type=int, default=None)
    sale_nearby.add_argument("--price-max", type=int, default=None)
    sale_nearby.add_argument("--rooms", type=int, nargs="*", default=[])

    demo = sub.add_parser("demo", help="full chain: tools -> status -> whoami -> search -> detail")
    demo.add_argument("--keyword", default="万象城")
    demo.add_argument("--page", type=int, default=1)
    demo.add_argument("--page-size", type=int, default=3)
    return parser


def main() -> None:
    ok = asyncio.run(_run(_parser().parse_args()))
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
