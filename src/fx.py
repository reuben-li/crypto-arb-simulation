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
from termcolor import colored
from IPython.display import clear_output

logging.basicConfig(filename='fx.log', level=logging.WARNING)

# globals
NO_TRADE = True
JUST_CLOSED = False
SIZE = 0.3
MARGIN = 99999
MARGIN2 = -99999
PROFIT_THRESHOLD = int(sys.argv[1])
ONCE = False if PROFIT_THRESHOLD > 0 else True
STABLE_VOL_FLOAT = SIZE * 3
LEVERAGE = 10

# import credentials
with open('config.yml', 'r') as ymlfile:
    cfg = yaml.load(ymlfile)
auth = cfg['auth']

# get last $$
with open('funds.json', 'r') as outfile:
    FUNDS = json.load(outfile)
LAST_TOTAL = FUNDS['funds']

with open('last_bf.json', 'r') as outfile:
    lbf = json.load(outfile)
LAST_BF = lbf['last_bf']

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
    return table, open_orders


def open_qn(direction, size=SIZE, leverage=LEVERAGE):
    qn_client.create_margin_order(
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
    res = bf_client.sendchildorder(
        product_code='FX_BTC_JPY',
        side=direction,
        child_order_type='MARKET',
        size=size
    )
    if 'child_order_acceptance_id' not in res.keys():
        raise Exception('BF ORDER FAILED!') from None
    global LAST_BF
    LAST_BF = direction
    loglog(json.dumps(res))


def order_status_qn():
    return sum(float(i['pnl']) for i in
               qn_client.get_trades(status='open')['models'])


def order_status_bf():
    res = bf_client.getcollateral()
    deposit = res['collateral']
    pnl = res['open_position_pnl']
    margin_used = res['require_collateral']
    return deposit + pnl, pnl, margin_used


def close_qn():
    qn_client.close_all_trades()


def close_bf():
    global LAST_BF
    if LAST_BF == 'SELL':
        open_bf('BUY')
    elif LAST_BF == 'BUY':
        open_bf('SELL')
    # elif LAST_BF == 'CLOSED':
    #    current_side = bf_client.getpositions(
    #        product_code='FX_BTC_JPY')[0]['side']
    #    side = 'SELL' if current_side == 'BUY' else 'BUY'
    #    open_bf(side)
    LAST_BF = 'CLOSED'


def loglog(msg):
    now = str(datetime.now().replace(
        microsecond=0) + timedelta(hours=9))
    logging.warning(now + ' ' + msg)


def get_last_profits():
    bp = 0.0
    bres = bf_client.getcollateralhistory()
    for b in bres:
        if b['date'] == bres[0]['date']:
            bp += float(b['change'])
        else:
            break
    qres = qn_client.get_trades(status='closed')
    for q in qres['models']:
        qp = float(q['close_pnl'])
        break
    profits = round(qp + bp, 2)
    prof_str = str(profits)
    if profits > 0:
        prof_str = colored(prof_str, 'white', 'on_green')
    else:
        prof_str = colored(prof_str, 'white', 'on_red')
    loglog('PROFIT: ' + str(qp) + '(qn) ' + str(bp) +
           '(bf) ' + prof_str + '(total)')


def qn_wrapper(q2):
    qn_pnl = order_status_qn()
    q2.put(qn_pnl)


def bf_wrapper(q2):
    d, bf_pnl, bu = order_status_bf()
    q2.put(bf_pnl)


def check_opp(action):
    total_pnl = 0
    if action == 'close':
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
                close_bf()
                close_qn()
                # t1 = threading.Thread(target=close_bf, args=())
                # t2 = threading.Thread(target=close_qn, args=())
                # t1.start()
                # t2.start()
                # t1.join()
                # t2.join()
                global JUST_CLOSED
                JUST_CLOSED = True
                loglog('CLOSED: expected pnl ' + str(total_pnl))
                if ONCE:
                    try:
                        get_last_profits()
                    except Exception as e1:
                        raise Exception('FAILED TO GET LAST PROFIT: '
                                        + str(e1))
                    sys.exit(1)
            except Exception as e2:
                raise Exception('CLOSE FAILED: ' + str(e2))
    global NO_TRADE
    qa, qb, ba, bb = get_prices()
    if action == 'open':
        if bb - qa > MARGIN:
            try:
                open_bf('SELL')
                open_qn('BUY')
                NO_TRADE = True
                # t1 = threading.Thread(target=open_bf, args=(['SELL']))
                # t2 = threading.Thread(target=open_qn, args=(['BUY']))
                # t1.start()
                # t2.start()
                # t1.join()
                # t2.join()
                loglog('buy qn:' + str(qa) + ' sell bf:' + str(bb) + ' (I)')
            except Exception as e:
                raise Exception('OPEN FAILED: ' + str(e))
        elif ba - qb < MARGIN2:
            try:
                open_bf('BUY')
                open_qn('SELL')
                NO_TRADE = True
                # t1 = threading.Thread(target=open_bf, args=(['BUY']))
                # t2 = threading.Thread(target=open_qn, args=(['SELL']))
                # t1.start()
                # t2.start()
                # t1.join()
                # t2.join()
                loglog('buy bf:' + str(ba) + ' sell qn:' + str(qb) + ' (II)')
            except Exception as e:
                raise Exception('OPEN FAILED: ' + str(e))

    data = {}
    data['bf_bid'] = bb
    data['bf_ask'] = ba
    data['qn_bid'] = qb
    data['qn_ask'] = qa
    return data, round(total_pnl, 2)


def main():
    bbqa_h = []
    baqb_h = []
    i = 0
    t_cnt = 0
    c_cnt = 0

    while True:
        os.system('clear')
        clear_output()
        global NO_TRADE
        global JUST_CLOSED
        if NO_TRADE:
            t_cnt += 1
        if t_cnt == 10:
            t_cnt = 0
            NO_TRADE = False
        if JUST_CLOSED:
            c_cnt += 1
        if c_cnt == 10:
            try:
                get_last_profits()
            except Exception as e1:
                raise Exception('FAILED TO GET LAST PROFIT: ' + str(e1))
            JUST_CLOSED = False
            c_cnt = 0

        table, open_orders = portfolio_value()
        if NO_TRADE:
            data, total_pnl = check_opp('wait')
        elif open_orders:
            data, total_pnl = check_opp('close')
        else:
            data, total_pnl = check_opp('open')

        current_bbqa = data['bf_bid'] - data['qn_ask']
        current_baqb = data['bf_ask'] - data['qn_bid']
        bbqa_h.append(current_bbqa)
        baqb_h.append(current_baqb)

        if i > 30:
            bbqa_h.pop(0)
            baqb_h.pop(0)

        max_bbqa = max(bbqa_h)
        min_baqb = min(baqb_h)
        global MARGIN
        global MARGIN2
        mid = (max_bbqa + min_baqb) / 2
        bound = PROFIT_THRESHOLD / 1.5
        MARGIN = mid + bound
        MARGIN2 = mid - bound

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

        prof_str = str(total_pnl)
        if total_pnl >= 0:
            prof_str = colored(prof_str, 'white', 'on_green')
        else:
            prof_str = colored(prof_str, 'white', 'on_red')

        print('total:', total, '(' + prof_str + ')')
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
        print('MARGIN:', MARGIN, '(' + str(round(current_bbqa, 2)) + ')')
        print('MARGIN2:', MARGIN2, '(' + str(round(current_baqb, 2)) + ')')
        print('LAST_BF:', LAST_BF)
        print('NO_TRADE:', NO_TRADE)

        print('PROFIT_THRESHOLD:', PROFIT_THRESHOLD)

        print('-------')
        print('RECENT TRADES')
        print('-------')
        for line in tailer.tail(open('fx.log'), 8):
            print(line.replace('WARNING:root:', ''))

        global FUNDS
        FUNDS = {'funds': total}
        i += 1
        time.sleep(4)


if __name__ == "__main__":
    while True:
        try:
            main()
        except KeyboardInterrupt:
            with open('funds.json', 'w') as outfile:
                json.dump(FUNDS, outfile)
            with open('last_bf.json', 'w') as outfile2:
                json.dump({'last_bf': LAST_BF}, outfile2)
                sys.exit(1)
        except Exception as e:
            e = 'connection issues' if 'Connection' in str(e) else e
            loglog('EXCEPTION: ' + str(e))
        time.sleep(10)
