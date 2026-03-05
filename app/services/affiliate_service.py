"""
Affiliate link generation and click tracking.

Supported networks (configurable via environment variables):
- Amazon Associates: rewrites amazon.com URLs with affiliate tag
- ShopStyle (Collective): rewrites shopstyle.com URLs with pid/uid
- Rakuten: rewrites tracking domains with siteID

Each network is enabled only when its required env var is set. If no network
matches a given product_url, the original URL is returned unchanged.

Click tracking
--------------
Every affiliate redirect goes through ``GET /r/{click_id}`` which:
  1. Looks up the pre-generated affiliate_clicks row by click_id
  2. Records clicked_at timestamp
  3. Issues an HTTP 302 to the affiliate URL

This gives us a server-side click log even when the browser blocks third-party
tracking pixels.
"""

import logging
import re
import uuid
from typing import Optional
from urllib.parse import urlencode, urlparse, urlunparse, parse_qs, urljoin

from app.services.db_service import get_connection

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Network implementations
# ---------------------------------------------------------------------------


def _rewrite_amazon(url: str, affiliate_tag: str) -> Optional[str]:
    """
    Append ?tag=<affiliate_tag> to amazon.com product URLs.

    Amazon Associates requires an ``asin`` to be in the URL path or query.
    Accepts standard product page URLs:
        https://www.amazon.com/dp/B09XYZ
        https://www.amazon.com/product-title/dp/B09XYZ/ref=...

    Returns None if the URL is not recognised as an Amazon product page.
    """
    parsed = urlparse(url)
    if "amazon.com" not in parsed.netloc:
        return None

    # ASIN appears after /dp/ or /product/ in Amazon URLs
    if not re.search(r"/(dp|product)/[A-Z0-9]{10}", parsed.path):
        return None

    # Strip existing tag parameter, then add ours
    qs = parse_qs(parsed.query, keep_blank_values=True)
    qs["tag"] = [affiliate_tag]
    new_query = urlencode({k: v[0] for k, v in qs.items()})
    rewritten = urlunparse(parsed._replace(query=new_query))
    logger.debug("Amazon affiliate rewrite: %s → %s", url[:60], rewritten[:60])
    return rewritten


def _rewrite_shopstyle(url: str, publisher_id: str) -> Optional[str]:
    """
    Append ShopStyle Collective affiliate parameters to shopstyle.com links.

    ShopStyle expects: ?pid=<publisher_id>&uid=<click_id>
    The uid is a per-click random value we generate at rewrite time.
    """
    parsed = urlparse(url)
    if "shopstyle" not in parsed.netloc:
        return None

    qs = parse_qs(parsed.query, keep_blank_values=True)
    qs["pid"] = [publisher_id]
    qs["uid"] = [str(uuid.uuid4())]
    new_query = urlencode({k: v[0] for k, v in qs.items()})
    rewritten = urlunparse(parsed._replace(query=new_query))
    logger.debug("ShopStyle affiliate rewrite: %s → %s", url[:60], rewritten[:60])
    return rewritten


def _rewrite_rakuten(url: str, site_id: str, mid: str) -> Optional[str]:
    """
    Build a Rakuten affiliate link for supported merchant domains.

    Rakuten uses a redirect URL format:
        https://click.linksynergy.com/deeplink?id=<site_id>&mid=<mid>&murl=<encoded_url>

    Args:
        url:     Original merchant URL.
        site_id: Rakuten publisher site ID.
        mid:     Merchant ID (Rakuten assigns one per merchant).

    Returns None if mid is not configured (we only rewrite for known merchants).
    """
    if not mid:
        return None

    from urllib.parse import quote

    rakuten_base = "https://click.linksynergy.com/deeplink"
    affiliate_url = f"{rakuten_base}?id={site_id}&mid={mid}&murl={quote(url, safe='')}"
    logger.debug("Rakuten affiliate rewrite: %s → %s", url[:60], affiliate_url[:60])
    return affiliate_url


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def rewrite_to_affiliate_url(
    product_url: str,
    amazon_tag: Optional[str] = None,
    shopstyle_pid: Optional[str] = None,
    rakuten_site_id: Optional[str] = None,
    rakuten_mid: Optional[str] = None,
) -> str:
    """
    Attempt to rewrite ``product_url`` to an affiliate URL.

    Tries each configured network in order: Amazon → ShopStyle → Rakuten.
    Returns the first successful rewrite, or the original URL if no network
    recognises it.

    Args:
        product_url:     Original product page URL.
        amazon_tag:      Amazon Associates tag, e.g. ``fitted-20``.
        shopstyle_pid:   ShopStyle Collective publisher ID.
        rakuten_site_id: Rakuten publisher site ID.
        rakuten_mid:     Rakuten merchant ID for the URL's domain.

    Returns:
        Affiliate URL string, or ``product_url`` unchanged.
    """
    if amazon_tag:
        rewritten = _rewrite_amazon(product_url, amazon_tag)
        if rewritten:
            return rewritten

    if shopstyle_pid:
        rewritten = _rewrite_shopstyle(product_url, shopstyle_pid)
        if rewritten:
            return rewritten

    if rakuten_site_id and rakuten_mid:
        rewritten = _rewrite_rakuten(product_url, rakuten_site_id, rakuten_mid)
        if rewritten:
            return rewritten

    return product_url


def get_affiliate_config() -> dict:
    """
    Load affiliate network credentials from environment / SSM config.

    Returns a dict with keys matching ``rewrite_to_affiliate_url`` kwargs.
    Missing or empty env vars produce None values (network disabled).
    """
    import os

    return {
        "amazon_tag": os.environ.get("AMAZON_AFFILIATE_TAG") or None,
        "shopstyle_pid": os.environ.get("SHOPSTYLE_PUBLISHER_ID") or None,
        "rakuten_site_id": os.environ.get("RAKUTEN_SITE_ID") or None,
        "rakuten_mid": os.environ.get("RAKUTEN_MID") or None,
    }


# ---------------------------------------------------------------------------
# Click tracking (DB)
# ---------------------------------------------------------------------------


async def record_affiliate_click(
    user_id: str,
    item_id: str,
    original_url: str,
    affiliate_url: str,
    network: str,
) -> str:
    """
    Insert a row into ``affiliate_clicks`` and return the click_id.

    The click_id is used in the ``/r/{click_id}`` redirect endpoint to look
    up the affiliate URL without exposing it in the frontend HTML (avoids
    adblocker URL pattern matching on affiliate query params).

    Args:
        user_id:       UUID of the authenticated user.
        item_id:       Catalog item ID.
        original_url:  Pre-rewrite product URL.
        affiliate_url: Post-rewrite affiliate URL.
        network:       ``'amazon'`` | ``'shopstyle'`` | ``'rakuten'`` | ``'none'``.

    Returns:
        click_id (UUID str) for the ``/r/{click_id}`` redirect.
    """
    click_id = str(uuid.uuid4())
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO affiliate_clicks
                    (click_id, user_id, item_id, original_url, affiliate_url, network)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (click_id, user_id, item_id, original_url, affiliate_url, network),
            )
            await conn.commit()
    logger.info(
        "affiliate_service.record_click: click_id=%s user_id=%s item_id=%s network=%s",
        click_id,
        user_id,
        item_id,
        network,
    )
    return click_id


async def resolve_and_record_click(click_id: str) -> Optional[str]:
    """
    Mark an affiliate_clicks row as clicked and return its affiliate URL.

    Used by ``GET /r/{click_id}`` to finalise the redirect.

    Args:
        click_id: UUID from the redirect URL.

    Returns:
        affiliate_url string if the click_id exists, else None.
    """
    async with get_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE affiliate_clicks
                SET clicked_at = NOW()
                WHERE click_id = %s AND clicked_at IS NULL
                RETURNING affiliate_url
                """,
                (click_id,),
            )
            row = await cur.fetchone()
            if row:
                await conn.commit()

    if row is None:
        logger.debug(
            "affiliate_service.resolve: click_id=%s not found or already clicked",
            click_id,
        )
        return None

    affiliate_url = row[0]
    logger.info(
        "affiliate_service.resolve: click_id=%s → %s", click_id, affiliate_url[:60]
    )
    return affiliate_url


def detect_network(url: str) -> str:
    """
    Infer the affiliate network name from the product URL domain.

    Returns ``'amazon'``, ``'shopstyle'``, ``'rakuten'``, or ``'none'``.
    """
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    if "amazon.com" in netloc:
        return "amazon"
    if "shopstyle" in netloc:
        return "shopstyle"
    if "linksynergy.com" in netloc:
        return "rakuten"
    return "none"
