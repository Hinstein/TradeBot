from __future__ import annotations

import time
from decimal import Decimal, ROUND_DOWN
from typing import Literal

from binance.um_futures import UMFutures
from binance.error import ClientError
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

Side = Literal["long", "short"]


class TradeError(Exception):
    pass


def make_client(api_key: str, api_secret: str, testnet: bool) -> UMFutures:
    base_url = "https://testnet.binancefuture.com" if testnet else "https://fapi.binance.com"
    client = UMFutures(key=api_key, secret=api_secret, base_url=base_url)
    retry = Retry(
        total=4,
        backoff_factor=0.6,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "POST", "DELETE", "PUT"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    client.session.mount("https://", adapter)
    client.session.mount("http://", adapter)
    return client


def _filter_value(symbol_info: dict, filter_type: str, key: str) -> Decimal:
    for f in symbol_info["filters"]:
        if f["filterType"] == filter_type:
            return Decimal(f[key])
    raise KeyError(f"{filter_type}.{key} not found")


def _round_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).quantize(Decimal("1"), rounding=ROUND_DOWN) * step


def _resolve_symbol(client: UMFutures, token: str) -> dict:
    candidate = token.upper().strip()
    if not candidate.endswith("USDT"):
        candidate = f"{candidate}USDT"
    info = client.exchange_info()
    for s in info["symbols"]:
        if s["symbol"] == candidate and s["status"] == "TRADING" and s["contractType"] == "PERPETUAL":
            return s
    raise TradeError(f"symbol not found or not trading: {candidate}")


def _setup(client: UMFutures, symbol: str, leverage: int, margin_type: str) -> None:
    positions = client.get_position_risk(symbol=symbol, recvWindow=10000)
    pos = positions[0] if positions else {}
    has_position = Decimal(pos.get("positionAmt", "0")) != 0
    current_lev = int(pos.get("leverage", "0") or 0)
    current_margin_type = (pos.get("marginType", "") or "").upper()

    if has_position:
        return

    if current_margin_type != margin_type.upper():
        try:
            client.change_margin_type(symbol=symbol, marginType=margin_type, recvWindow=10000)
        except ClientError as e:
            if e.error_code != -4046:
                raise

    if current_lev != leverage:
        client.change_leverage(symbol=symbol, leverage=leverage, recvWindow=10000)


def _avg_fill_price(client: UMFutures, symbol: str, order: dict) -> Decimal:
    avg = order.get("avgPrice")
    if avg and Decimal(avg) > 0:
        return Decimal(avg)
    for _ in range(5):
        time.sleep(0.3)
        q = client.query_order(symbol=symbol, orderId=order["orderId"])
        if q.get("avgPrice") and Decimal(q["avgPrice"]) > 0:
            return Decimal(q["avgPrice"])
    return Decimal(client.mark_price(symbol=symbol)["markPrice"])


def _emergency_close(client: UMFutures, symbol: str, side: str) -> bool:
    close_side = "SELL" if side == "BUY" else "BUY"
    for attempt in range(3):
        try:
            positions = client.get_position_risk(symbol=symbol, recvWindow=10000)
            amt = Decimal(positions[0]["positionAmt"])
            if amt == 0:
                return True
            client.new_order(
                symbol=symbol,
                side=close_side,
                type="MARKET",
                quantity=format(abs(amt), "f"),
                reduceOnly="true",
                recvWindow=10000,
            )
            return True
        except Exception:
            time.sleep(0.5 * (attempt + 1))
    return False


def cancel_order(client: UMFutures, symbol: str, order_id: int) -> None:
    """Cancel a regular order, ignoring already-filled/cancelled errors."""
    try:
        client.cancel_order(symbol=symbol, orderId=order_id, recvWindow=10000)
    except ClientError as e:
        if e.error_code not in (-2011, -2013):
            raise


def cancel_algo_order(client: UMFutures, symbol: str, algo_id: int) -> None:
    """Cancel an algo order, ignoring already-filled/cancelled errors."""
    try:
        client.sign_request("DELETE", "/fapi/v1/algoOrder", {
            "algoId": algo_id,
            "recvWindow": 10000,
        })
    except ClientError as e:
        if e.error_code not in (-2011, -2013):
            raise


def place_sl_market(client: UMFutures, symbol: str, close_side: str,
                    sl_price: Decimal, tick: Decimal) -> int:
    """Place a STOP_MARKET algo order covering the full position. Returns algoId."""
    sl_price = _round_step(sl_price, tick)
    resp = client.sign_request("POST", "/fapi/v1/algoOrder", {
        "algoType": "CONDITIONAL",
        "symbol": symbol,
        "side": close_side,
        "type": "STOP_MARKET",
        "triggerPrice": format(sl_price, "f"),
        "closePosition": "true",
        "workingType": "MARK_PRICE",
        "recvWindow": 10000,
    })
    return resp.get("algoId") or resp.get("orderId")


def execute_trade(
    client: UMFutures,
    token: str,
    side: Side,
    leverage: int,
    margin_usdt: float,
    tp_pct: float,
    sl_pct: float,
    margin_type: str = "ISOLATED",
    split_tp: bool = False,
    tp1_pct: float = 0.0,
    tp2_pct: float = 0.0,
) -> dict:
    import requests

    def retry_call(fn, *, attempts: int = 3):
        last = None
        for i in range(attempts):
            try:
                return fn()
            except (requests.exceptions.SSLError,
                    requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout) as ex:
                last = ex
                time.sleep(0.5 * (i + 1))
        raise TradeError(f"network flaky after {attempts} retries: {last}")

    symbol_info = retry_call(lambda: _resolve_symbol(client, token))
    symbol = symbol_info["symbol"]
    order_side = "BUY" if side == "long" else "SELL"
    close_side = "SELL" if order_side == "BUY" else "BUY"

    retry_call(lambda: _setup(client, symbol, leverage, margin_type))

    step = _filter_value(symbol_info, "LOT_SIZE", "stepSize")
    tick = _filter_value(symbol_info, "PRICE_FILTER", "tickSize")
    mark = Decimal(retry_call(lambda: client.mark_price(symbol=symbol))["markPrice"])
    notional = Decimal(str(margin_usdt)) * Decimal(leverage)
    qty = _round_step(notional / mark, step)
    if qty <= 0:
        raise TradeError(f"qty rounded to 0 for {symbol} — margin too small at price {mark}")

    order = retry_call(lambda: client.new_order(
        symbol=symbol,
        side=order_side,
        type="MARKET",
        quantity=format(qty, "f"),
        newOrderRespType="RESULT",
    ))

    try:
        entry = _avg_fill_price(client, symbol, order)
        direction = Decimal(1) if order_side == "BUY" else Decimal(-1)
        sl_price = _round_step(entry * (Decimal(1) - direction * Decimal(str(sl_pct)) / Decimal(100)), tick)

        # place stop loss (covers full position)
        sl_id = retry_call(lambda: place_sl_market(client, symbol, close_side, sl_price, tick))

        if split_tp:
            # two reduceOnly limit orders, each covering half the qty
            half_qty = _round_step(qty / Decimal(2), step)
            if half_qty <= 0:
                raise TradeError("qty too small to split into two TP orders")

            tp1_price = _round_step(entry * (Decimal(1) + direction * Decimal(str(tp1_pct)) / Decimal(100)), tick)
            tp2_price = _round_step(entry * (Decimal(1) + direction * Decimal(str(tp2_pct)) / Decimal(100)), tick)

            tp1_order = retry_call(lambda: client.new_order(
                symbol=symbol,
                side=close_side,
                type="LIMIT",
                price=format(tp1_price, "f"),
                quantity=format(half_qty, "f"),
                timeInForce="GTC",
                reduceOnly="true",
                recvWindow=10000,
            ))
            tp2_order = retry_call(lambda: client.new_order(
                symbol=symbol,
                side=close_side,
                type="LIMIT",
                price=format(tp2_price, "f"),
                quantity=format(half_qty, "f"),
                timeInForce="GTC",
                reduceOnly="true",
                recvWindow=10000,
            ))

            return {
                "symbol": symbol,
                "side": side,
                "qty": str(qty),
                "entry": str(entry),
                "sl_price": str(sl_price),
                "leverage": leverage,
                "margin": margin_usdt,
                "split_tp": True,
                "tp1_price": str(tp1_price),
                "tp2_price": str(tp2_price),
                "tp1_order_id": tp1_order["orderId"],
                "tp2_order_id": tp2_order["orderId"],
                "sl_id": sl_id,
            }
        else:
            tp_price = _round_step(entry * (Decimal(1) + direction * Decimal(str(tp_pct)) / Decimal(100)), tick)
            tp_resp = retry_call(lambda: client.sign_request("POST", "/fapi/v1/algoOrder", {
                "algoType": "CONDITIONAL",
                "symbol": symbol,
                "side": close_side,
                "type": "TAKE_PROFIT_MARKET",
                "triggerPrice": format(tp_price, "f"),
                "closePosition": "true",
                "workingType": "MARK_PRICE",
                "recvWindow": 10000,
            }))
            return {
                "symbol": symbol,
                "side": side,
                "qty": str(qty),
                "entry": str(entry),
                "tp_price": str(tp_price),
                "sl_price": str(sl_price),
                "leverage": leverage,
                "margin": margin_usdt,
                "split_tp": False,
                "open_order_id": order["orderId"],
                "tp_id": tp_resp.get("algoId") or tp_resp.get("orderId"),
                "sl_id": sl_id,
            }

    except Exception as ex:
        closed = _emergency_close(client, symbol, order_side)
        if closed:
            raise TradeError(f"tp/sl failed, position closed: {ex}")
        raise TradeError(
            f"🚨🚨 CRITICAL: tp/sl failed AND emergency close FAILED for {symbol}. "
            f"GO TO BINANCE APP AND CLOSE {symbol} MANUALLY NOW. "
            f"Original error: {ex}"
        )
