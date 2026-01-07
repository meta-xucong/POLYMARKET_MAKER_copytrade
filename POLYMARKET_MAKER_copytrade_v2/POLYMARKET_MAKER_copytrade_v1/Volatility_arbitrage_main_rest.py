from __future__ import annotations

import os
from typing import Any

from py_clob_client.client import ClobClient

DEFAULT_HOST = "https://clob.polymarket.com"
DEFAULT_CHAIN_ID = 137
DEFAULT_SIGNATURE_TYPE = 2

_CLIENT_SINGLETON: ClobClient | None = None


def _normalize_privkey(k: str) -> str:
    return k[2:] if k.startswith(("0x", "0X")) else k


def init_client() -> ClobClient:
    host = os.getenv("POLY_HOST", DEFAULT_HOST)
    chain_id = int(os.getenv("POLY_CHAIN_ID", str(DEFAULT_CHAIN_ID)))
    signature_type = int(os.getenv("POLY_SIGNATURE", str(DEFAULT_SIGNATURE_TYPE)))

    key = _normalize_privkey(os.environ["POLY_KEY"])
    funder = os.environ["POLY_FUNDER"]

    client = ClobClient(
        host,
        key=key,
        chain_id=chain_id,
        signature_type=signature_type,
        funder=funder,
    )
    api_creds = client.create_or_derive_api_creds()
    client.set_api_creds(api_creds)
    try:
        setattr(client, "api_creds", api_creds)
    except Exception:
        pass
    return client


def get_client() -> ClobClient:
    global _CLIENT_SINGLETON
    if _CLIENT_SINGLETON is None:
        _CLIENT_SINGLETON = init_client()
    return _CLIENT_SINGLETON


if __name__ == "__main__":
    client = get_client()
    print(
        "[INIT] ClobClient 就绪。host=%s chain_id=%s signature_type=%s funder=%s"
        % (
            os.getenv("POLY_HOST", DEFAULT_HOST),
            os.getenv("POLY_CHAIN_ID", str(DEFAULT_CHAIN_ID)),
            os.getenv("POLY_SIGNATURE", str(DEFAULT_SIGNATURE_TYPE)),
            os.environ.get("POLY_FUNDER", "?")[:10] + "...",
        )
    )
