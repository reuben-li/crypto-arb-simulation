from zaifapi import *
from quoine.client import Quoinex
import time
import json
import yaml
import threading
from datetime import datetime, timedelta
import logging
import os
import tailer

logging.basicConfig(filename='trade.log', level=logging.WARNING)

# globals
SIZE = 0.2
MARGIN = 3000
MIN_MARGIN = 1500
STABLE_VOL_FLOAT = 0.5
LEVERAGE = 5
QN_MC = 0.15
ZF_MC = 0.35

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
    try:
        key = 'deposit_jpy'
        zf_used = next(iter(
            zf_pclient.active_positions(type='margin').items()))[1][key]
    except Exception:
        zf_used = 0
    table['zf_balance'] = zf_iclient.get_info2()['funds']['jpy'] + zf_used
    table['zf_margin'] = (table['zf_balance'] - zf_used) / table['zf_balance']
    return table


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


def close_qn():
    res = qn_client.get_trades(funding_currency='JPY', status='open')
    for lev in res['models']:
        qn_client.close_trade(lev['id'])


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


def clear_trade():
    with open('current_trade.json', 'w') as outfile:
        json.dump({'trade': ''}, outfile)


def check_opp(trade_ok):
    with open('current_trade.json', 'r') as infile:
        ct = json.load(infile)
    qa, qb = qn_price()
    za, zb = zf_price()
    if not ct['trade'] and trade_ok:
        if zb - qa > MARGIN:
            try:
                open_zf('SELL', zb)
                open_qn('BUY', '')
                logging.warning(
                    str(datetime.now() + timedelta(hours=9)) +
                    ' buy ' + 'qn' + ':' +
                    str(qa) + ' sell ' + 'zf' + ':' + str(zb)
                )
                with open('current_trade.json', 'w') as outfile:
                    json.dump({'trade': 'bqsz'}, outfile)
            except Exception:
                print('trade failed')
                pass
        elif qb - za > MARGIN:
            try:
                open_zf('BUY', za)
                open_qn('SELL', '')
                logging.warning(
                    str(datetime.now() + timedelta(hours=9)) +
                    ' buy ' + 'zf' + ':' +
                    str(za) + ' sell ' + 'qn' + ':' + str(qb)
                )
                with open('current_trade.json', 'w') as outfile:
                    json.dump({'trade': 'bzsq'}, outfile)
            except Exception:
                print('trade failed')
                pass
    else:
        if (ct['trade'] == 'bqsz') and (za - qb < MIN_MARGIN):
            try:
                close_zf()
                close_qn()
                clear_trade()
            except Exception:
                print('close failed')
                pass
        if (ct['trade'] == 'bzsq') and (qa - zb < MIN_MARGIN):
            try:
                close_zf()
                close_qn()
                clear_trade()
            except Exception:
                print('close failed')
                pass
    data = {}
    data['zf_bid'] = zb
    data['zf_ask'] = za
    data['qn_bid'] = qb
    data['qn_ask'] = qa

    d = sorted(data.items(), key=lambda kv: kv[1], reverse=True)
    print('-------')
    print('PRICES')
    print('-------')
    oldv = ''
    for k, v in d:
        if oldv:
            print(k, v, '(' + str(v-oldv) + ')')
        else:
            print(k, v)
        oldv = v
    print('')
    print('zf_ask - qn_bid:', za - qb)
    return


def main():
    i = 0
    with open('table.json') as data_file:
        old_table = json.load(data_file)

    while True:
        os.system('clear')
        i += 1
        table = portfolio_value()
        if table['qn_margin'] > QN_MC and table['zf_margin'] > ZF_MC:
            check_opp(True)
        else:
            check_opp(False)

        qn_pnl = table['qn_balance'] - old_table['qn_balance']
        zf_pnl = table['zf_balance'] - old_table['zf_balance']
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

        print('-------')
        print('RECENT TRADES')
        print('-------')
        for line in tailer.tail(open('trade.log'), 6):
            print(line.replace('WARNING:root:', ''))

        with open('table.json', 'w') as outfile:
            json.dump(table, outfile)

        time.sleep(4)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        time.sleep(10)
        main()