from zaifapi import *
from quoine.client import Quoinex
import time
import json
import yaml
from datetime import datetime, timedelta
import logging
import os
import sys
import tailer

logging.basicConfig(filename='trade.log', level=logging.WARNING)

# globals
SIZE = 0.2
MARGIN = 3000
PROFIT_THRESHOLD = 300 # in yen
STABLE_VOL_FLOAT = 0.5
LEVERAGE = 5
QN_MC = 0.15
ZF_MC = 0.35
HISTORY = 49

# import credentials
with open('config.yml', 'r') as ymlfile:
    cfg = yaml.load(ymlfile)
auth = cfg['auth']

# instantiate clients
zf_pclient = ZaifLeverageTradeApi(
    auth['zf']['key'], auth['zf']['secret'])
zf_iclient = ZaifTradeApi(
    auth['zf']['key'], auth['zf']['secret'])
zf_client = ZaifPublicApi()
qn_client = Quoinex(auth['qn']['key'], auth['qn']['secret'])


def qn_price():
    res = qn_client.get_order_book(5, full=True)
    ask, bid = stable_price(res, 'sell_price_levels', 'buy_price_levels')
    return float(ask), float(bid)


def zf_price():
    res = zf_client.depth('btc_jpy')
    ask, bid = stable_price(res, 'asks', 'bids')
    return int(ask), int(bid)


def stable_price(res, ask_key, bid_key):
    for ask, askv in res[ask_key]:
        if float(askv) > STABLE_VOL_FLOAT:
            ask = ask
            break
    for bid, bidv in res[bid_key]:
        if float(bidv) > STABLE_VOL_FLOAT:
            bid = bid
            return ask, bid


def portfolio_value():
    table = {}
    qn_res = qn_client.get_trading_account(1464512)
    table['qn_balance'] = float(qn_res['equity'])
    table['qn_margin'] = float(qn_res['free_margin']) / table['qn_balance']
    table['qn_pnl'] = float(qn_res['pnl'])
    zf_id, zf_pnl, zf_used = order_status_zf()
    table['zf_balance'] = zf_iclient.get_info2()['funds']['jpy'] + \
        zf_used + zf_pnl
    table['zf_margin'] = (table['zf_balance'] - zf_used) / table['zf_balance']
    table['zf_pnl'] = zf_pnl

    if table['qn_margin'] == 1.0 and table['zf_margin'] == 1.0:
        open_orders = False
    else:
        open_orders = True
    # XOR
    if bool(table['qn_margin'] == 1.0) ^ bool(table['zf_margin'] == 1.0):
        print('!!! UNBALANCE TRADING !!!')
        sys.quit(1)
    return table, open_orders


def open_qn(direction, price, size=SIZE, leverage=LEVERAGE):
    return qn_client.create_margin_order(
        order_type='market',
        product_id=5,
        side=direction.lower(),
        quantity=size,
        price='',
        leverage_level=leverage,
        funding_currency='JPY',
        order_direction='net-out'
    )


def open_zf(direction, price, size=SIZE, leverage=LEVERAGE):
    action = 'bid' if direction == 'BUY' else 'ask'
    zf_pclient.create_position(
        action=action,
        amount=size,
        leverage=leverage,
        price=price,
        type='margin',
        currency_pair='btc_jpy'
    )


def order_status_qn():
    res = qn_client.get_trades(funding_currency='JPY', status='open')
    order_id = ''
    pnl = 0
    margin_used = 0
    if res:
        order_id = res['models'][0]['id']
        pnl = res['models'][0]['open_pnl']
        margin_used = res['models'][0]['margin_used']
    return order_id, pnl, margin_used


def order_status_zf(ask, bid):
    order_id = ''
    pnl = 0
    margin_used = 0
    res = zf_pclient.active_positions(type='margin')
    if res:
        ask, bid = zf_price()
        for lev in res.items():
            order_id = int(lev[0])
            if lev[1]['action'] == 'ask':
                diff = lev[1]['price_avg'] - ask
            else:
                diff = bid - lev[1]['price_avg']
            pnl = diff * lev[1]['amount_done']
            margin_used = lev[1]['deposit_jpy']
            # taking only 1 item
            break
    return order_id, pnl, margin_used


def close_qn():
    res = qn_client.get_trades(funding_currency='JPY', status='open')
    for lev in res['models']:
        qn_client.close_trade(lev['id'])


def close_zf():
    res = zf_pclient.active_positions(type='margin')
    ask, bid = zf_price()
    for lev in res.items():
        if lev[1]['action'] == 'ask':
            limit = ask
        else:
            limit = bid
        zf_pclient.change_position(
            leverage_id=int(lev[0]),
            type='margin',
            limit=limit,
            price=int(lev[1]['price'])
        )


def check_opp(trade_ok):
    qa, qb = qn_price()
    za, zb = zf_price()
    if trade_ok:
        if zb - qa > MARGIN:
            try:
                open_zf('SELL', zb)
                open_qn('BUY', '')
                print('traded')
                logging.warning(
                    str(datetime.now() + timedelta(hours=9)) +
                    ' buy ' + 'qn' + ':' +
                    str(qa) + ' sell ' + 'zf' + ':' + str(zb)
                )
            except Exception:
                print('trade failed')
                pass
        elif qb - za > MARGIN:
            try:
                open_zf('BUY', za)
                open_qn('SELL', '')
                print('traded')
                logging.warning(
                    str(datetime.now() + timedelta(hours=9)) +
                    ' buy ' + 'zf' + ':' +
                    str(za) + ' sell ' + 'qn' + ':' + str(qb)
                )
            except Exception:
                print('trade failed')
                pass
    else:
        qn_id, qn_pnl, qn_used = order_status_qn()
        zf_id, zf_pnl, zf_used = order_status_zf()
        if qn_pnl + zf_pnl > PROFIT_THRESHOLD:
            try:
                close_zf()
                close_qn()
            except Exception:
                print('close failed')
                pass
    data = {}
    data['zf_bid'] = zb
    data['zf_ask'] = za
    data['qn_bid'] = qb
    data['qn_ask'] = qa
    return data


def main():
    max_zbqa = 0
    min_zaqb = 9999999

    while True:
        os.system('clear')
        table, open_orders = portfolio_value()
        if open_orders:
            data = check_opp(False)
        else:
            data = check_opp(True)

        current_zbqa = data['zf_bid'] - data['qn_ask']
        current_zaqb = data['zf_ask'] - data['qn_bid']
        max_zbqa = max(max_zbqa, current_zbqa)
        min_zaqb = max(min_zaqb, current_zaqb)

        qn_pnl = table['qn_pnl']
        zf_pnl = table['zf_pnl']
        print('-------')
        print('BALANCE')
        print('-------')

        print('qn:', round(table['qn_balance'], 2),
              '(' + str(round(qn_pnl, 2)) + ')')
        print('zf:', round(table['zf_balance'], 2),
              '(' + str(round(zf_pnl, 2)) + ')')
        print('total:', round(table['qn_balance'] + table['zf_balance'], 2),
              '(' + str(round(qn_pnl + zf_pnl, 2)) + ')')

        print('')
        print('free qn margin:', round(table['qn_margin'] * 100, 2), '%')
        print('free zf margin:', round(table['zf_margin'] * 100, 2), '%')

        d = sorted(data.items(), key=lambda kv: kv[1], reverse=True)
        print('-------')
        print('PRICES')
        print('-------')
        oldv = ''
        for k, v in d:
            if oldv:
                print(k, v, '(' + str(round(v-oldv, 2)) + ')')
            else:
                print(k, v)
            oldv = v
        print('')
        print('cur zbqa:', round(current_zbqa, 2))
        print('cur zaqb:', round(current_zaqb, 2))

        print('max zbqa:', round(max_zbqa, 2))
        print('min zaqb:', round(min_zaqb, 2))

        print('-------')
        print('RECENT TRADES')
        print('-------')
        for line in tailer.tail(open('trade.log'), 6):
            print(line.replace('WARNING:root:', ''))

        with open('table.json', 'w') as outfile:
            json.dump(table, outfile)

        time.sleep(5)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        time.sleep(10)
        main()