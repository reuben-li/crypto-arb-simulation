%matplotlib inline

import requests
import seaborn as sns
import time
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
from IPython import display
from datetime import datetime

BF_BASE = 'https://api.bitflyer.com/v1/'
BF_BOARD = BF_BASE + 'board?product_code=ETH_JPY'
QN_BASE ='https://api.liquid.com/'
QN_BOARD = QN_BASE + 'products/29/price_levels'

STABLE_VOL_FLOAT = 1

def bf_price():
  res = requests.get(BF_BOARD).json()
  for a in res['asks']:
    if a['size'] > STABLE_VOL_FLOAT:
      ask = a['price']
      break
  for b in res['bids']:
    if b['size'] > STABLE_VOL_FLOAT:
      bid = b['price']
      break
  return ask, bid

def qn_price():
  res = requests.get(QN_BOARD).json()
  for a in res['sell_price_levels']:
    if float(a[1]) > STABLE_VOL_FLOAT:
      ask = float(a[0])
      break
  for b in res['buy_price_levels']:
    if float(b[1]) > STABLE_VOL_FLOAT:
      bid = float(b[0])
      break
  return ask, bid

plt.style.use('ggplot')

count = 0
bf_asks = []
bf_bids = []
qn_asks = []
qn_bids = []
dates = []
line1 = []
while True:
  if count > 50:
    break
  dates.append(datetime.now().strftime("%Y%m%d_%H%M%S"))
  bf_ask, bf_bid = bf_price()
  qn_ask, qn_bid = qn_price()
  bf_asks.append(bf_ask)
  bf_bids.append(bf_bid)
  qn_asks.append(qn_ask)
  qn_bids.append(qn_bid)
  #plt.ion()
  fig = plt.figure(figsize=(13,6))
  ax = fig.add_subplot(111)
  ax.plot(dates,qn_asks,'b-',alpha=0.8, label='qn_ask')
  ax.plot(dates,qn_bids,'b:',alpha=0.8, label='qn_bid')        
  ax.plot(dates,bf_asks,'r-',alpha=0.8, label='bf_ask')        
  ax.plot(dates,bf_bids,'r:',alpha=0.8, label='bf_bid')   
  ax.fill_between(dates, bf_asks, qn_bids, where=(bf_asks<qn_bids), color='green', alpha=0.2)
  ax.legend(bbox_to_anchor=(1.1, 1.05)) 
          
  plt.ylabel('ETH_JPY')
  plt.xticks(rotation=90)
  display.clear_output(wait=True)
  plt.pause(2)

  count+=1
  #time.sleep(1)
