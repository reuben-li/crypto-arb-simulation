from quoine.client import Quoinex
import time
import yaml
from datetime import datetime, timedelta
import logging
import os
import sys
import tailer
import json
import pybitflyer
import threading
from queue import Queue
from IPython.display import clear_output

logging.basicConfig(filename='fx.log', level=logging.WARNING)

# globals
SIZE = 0.3
MARGIN = 16000
MARGIN2 = -1600
PROFIT_THRESHOLD = int(sys.argv[1]) # in yen
STABLE_VOL_FLOAT = SIZE * 2 
LEVERAGE = 10

# import credentials
with open('config.yml', 'r') as ymlfile:
    cfg = yaml.load(ymlfile)
auth = cfg['auth']

# get last $$
with open('funds.json', 'r') as outfile:
    FUNDS = json.load(outfile)
LAST_TOTAL = FUNDS['funds']

# instantiate clients
bf_client = pybitflyer.API(
    api_key=auth['bf']['key'], api_secret=auth['bf']['secret'])
qn_client = Quoinex(auth['qn']['key'], auth['qn']['secret'])


def qn_price(q):
    res = qn_client.get_order_book(5, full=True)
    ask, bid = stable_price(res, 'sell_price_levels', 'buy_price_levels')
    q.put({'qn': (float(ask), float(bid))})


def bf_price(q):
    res = bf_client.board(product_code='FX_BTC_JPY')
    for a in res['asks']:
        if a['size'] > STABLE_VOL_FLOAT:
            ask = a['price']
            break
    for b in res['bids']:
        if b['size'] > STABLE_VOL_FLOAT:
            bid = b['price']
            q.put({'bf': (ask, bid)})
            break


def stable_price(res, ask_key, bid_key):
    for ask, askv in res[ask_key]:
        if float(askv) > STABLE_VOL_FLOAT:
            ask = ask
            break
    for bid, bidv in res[bid_key]:
        if float(bidv) > STABLE_VOL_FLOAT:
            bid = bid
            return ask, bid


def get_prices():
    q = Queue()
    t1 = threading.Thread(target=qn_price, args=([q]))
    t2 = threading.Thread(target=bf_price, args=([q]))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    p = {}
    for i in range(2):
        p.update(q.get())
    return p['qn'][0], p['qn'][1], p['bf'][0], p['bf'][1]


def portfolio_value():
    table = {}
    qn_res = qn_client.get_trading_account(1464512)
    table['qn_balance'] = float(qn_res['equity'])
    table['qn_margin'] = float(qn_res['free_margin']) / table['qn_balance']
    table['qn_pnl'] = float(qn_res['pnl'])
    deposit, bf_pnl, bf_used = order_status_bf()
    table['bf_balance'] = deposit
    table['bf_margin'] = 1 - bf_used/deposit if deposit > 0 else 1.0
    table['bf_pnl'] = bf_pnl

    if table['qn_margin'] == 1.0 and table['bf_margin'] == 1.0:
        open_orders = False
    else:
        open_orders = True
    # XOR
    #if bool(table['qn_margin'] == 1.0) ^ bool(table['zf_margin'] == 1.0):
    #    print('!!! UNBALANCE TRADING !!!')
    #    sys.exit(1)
    return table, open_orders


def open_qn(direction, size=SIZE, leverage=LEVERAGE):
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


def open_bf(direction, size=SIZE):
    bf_client.sendchildorder(
        product_code='FX_BTC_JPY',
        side=direction,
        child_order_type='MARKET',
        size=size
    )


def order_status_qn():
    res = qn_client.get_trading_account(1464512)
    return float(res['pnl'])


def order_status_bf():
    res = bf_client.getcollateral()
    deposit = res['collateral']
    pnl = res['open_position_pnl']
    margin_used = res['require_collateral']
    return deposit + pnl, pnl, margin_used


def close_qn():
    qn_client.close_all_trades()


def close_bf():
    open_dir = bf_client.getpositions(product_code='FX_BTC_JPY')[0]['side']
    close_dir = 'BUY' if open_dir == 'SELL' else 'SELL'
    # get to be safe
    open_bf(close_dir)


def loglog(msg):
    now = str(datetime.now().replace(
        microsecond=0) + timedelta(hours=9))
    logging.warning(now + ' ' + msg)


def get_last_profits():
    b_profit = 0.0
    bres = bf_client.getcollateralhistory()
    for b in bres:
        if b['date'] == bres[0]['date']:
            b_profit += float(b['change'])
        else:
            break
    qres = qn_client.get_trades(status='closed')
    for q in qres['models']:
        q_profit = float(q['close_pnl'])
        break
    return round(q_profit, 2), b_profit


def bf_wrapper(q2):
    qn_pnl = order_status_qn()
    q2.put(qn_pnl)


def qn_wrapper(q2):
    d, bf_pnl, bu = order_status_bf()
    q2.put(bf_pnl)


def check_opp(trade_ok):
    if not trade_ok:
        total_pnl = 0
        q = Queue()
        t1 = threading.Thread(target=qn_wrapper, args=([q]))
        t2 = threading.Thread(target=bf_wrapper, args=([q]))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        for i in range(2):
            total_pnl += q.get()

        if total_pnl > PROFIT_THRESHOLD:
            try:
                close_qn()
                close_bf()
                #t1 = threading.Thread(target=close_bf, args=())
                #t2 = threading.Thread(target=close_qn, args=())
                #t1.start()
                #t2.start()
                #t1.join()
                #t2.join()
                time.sleep(4)
                qp, bp = get_last_profits()
                loglog('CLOSED: ' + str(qp) + '(qn) ' + str(bp) +
                       '(bf) ' + str(round(qp + bp, 2)) + '(total)')

            except Exception as e:
                loglog('CLOSE FAILED: ' + str(e))
                pass
    qa, qb, ba, bb = get_prices()
    if trade_ok:
        if bb - qa > MARGIN:
            try:
                open_qn('BUY')
                open_bf('SELL')
                #t1 = threading.Thread(target=open_bf, args=(['SELL']))
                #t2 = threading.Thread(target=open_qn, args=(['BUY']))
                #t1.start()
                #t2.start()
                #t1.join()
                #t2.join()
                loglog('buy qn:' + str(qa) + ' sell bf:' + str(bb))
            except Exception as e:
                loglog('TRADE FAILED: ' + str(e))
                pass
        elif qb - ba > MARGIN2:
            try:
                open_qn('SELL')
                open_bf('BUY')
                #t1 = threading.Thread(target=open_bf, args=(['BUY']))
                #t2 = threading.Thread(target=open_qn, args=(['SELL']))
                #t1.start()
                #t2.start()
                #t1.join()
                #t2.join()
                loglog('buy bf:' + str(ba) + ' sell qn:' + str(qb))
            except Exception as e:
                loglog('TRADE FAILED: ' + str(e))
                pass

    data = {}
    data['bf_bid'] = bb
    data['bf_ask'] = ba
    data['qn_bid'] = qb
    data['qn_ask'] = qa
    return data


def main():
    max_bbqa = -99999
    min_bbqa = 99999
    min_baqb = 99999
    max_baqb = -99999

    while True:
        os.system('clear')
        clear_output()
        table, open_orders = portfolio_value()
        if open_orders:
            data = check_opp(False)
        else:
            data = check_opp(True)

        current_bbqa = data['bf_bid'] - data['qn_ask']
        current_baqb = data['bf_ask'] - data['qn_bid']
        max_bbqa = max(max_bbqa, current_bbqa)
        min_bbqa = min(min_bbqa, -current_bbqa)
        min_baqb = min(min_baqb, current_baqb)
        max_baqb = max(max_baqb, -current_baqb)
        global MARGIN
        global MARGIN2
        if max_bbqa > min_baqb and max_bbqa > MARGIN + 100:
            MARGIN = max_bbqa
            max_bbqa = -99999
            min_baqb = 99999
        elif max_baqb > min_bbqa and max_baqb > MARGIN2 + 100:
            MARGIN2 = max_baqb
            min_bbqa = 99999
            max_baqb = -99999

        qn_pnl = table['qn_pnl']
        bf_pnl = table['bf_pnl']
        print('-------')
        print('BALANCE')
        print('-------')

        print('qn:', round(table['qn_balance'], 2),
              '(' + str(round(qn_pnl, 2)) + ')')
        print('bf:', round(table['bf_balance'], 2),
              '(' + str(round(bf_pnl, 2)) + ')')
        total = round(table['qn_balance'] + table['bf_balance'], 2)
        total_pnl = round(qn_pnl + bf_pnl, 2)
        print('total:', total, '(' + str(total_pnl) + ')')
        ses_pnl = round(total - LAST_TOTAL, 2)
        ses_pnl_per = round(ses_pnl / LAST_TOTAL * 100, 2)
        print('session pnl:', ses_pnl, '(' + str(ses_pnl_per) + '%)')
        print('')
        print('free qn margin:', round(table['qn_margin'] * 100, 2), '%')
        print('free bf margin:', round(table['bf_margin'] * 100, 2), '%')

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
        print('max_bbqa-min_baqb:', round(max_bbqa - min_baqb, 2))
        print('max_baqb-min_bbqa:', round(max_baqb - min_bbqa, 2))
        print('MARGIN:', MARGIN, '(' + str(round(current_bbqa, 2)) + ')')
        print('MARGIN2:', MARGIN2, '(' + str(round(-current_baqb, 2)) + ')')

        print('PROFIT_THRESHOLD:', PROFIT_THRESHOLD)

        print('-------')
        print('RECENT TRADES')
        print('-------')
        for line in tailer.tail(open('fx.log'), 8):
            print(line.replace('WARNING:root:', ''))

        global FUNDS
        FUNDS = {'funds': total}

        time.sleep(3.5)


if __name__ == "__main__":
    while True:
        try:
            main()
        except KeyboardInterrupt:
            with open('funds.json', 'w') as outfile:
                json.dump(FUNDS, outfile)
                sys.exit(1)
        except Exception as e:
            e = 'connection issues' if 'Connection' in str(e) else e
            loglog('EXCEPTION: ' + str(e))
        time.sleep(10)
