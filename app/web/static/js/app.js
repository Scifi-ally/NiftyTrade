/**
 * NiftyTrades - Real-time TradingView Lightweight Charts Frontend
 */

(function () {
  'use strict';

  // State
  let chart = null;
  let candleSeries = null;
  let activeToken = null;
  let activeContractInfo = null;
  let contracts = {};
  let ws = null;
  let markers = [];
  let entryLine = null;
  let stopLine = null;
  let targetLine = null;

  // DOM Elements
  const elTotalPnl = document.getElementById('total-pnl');
  const elRealisedPnl = document.getElementById('realised-pnl');
  const elUnrealisedPnl = document.getElementById('unrealised-pnl');
  const elFeedDot = document.getElementById('feed-status-dot');
  const elFeedText = document.getElementById('feed-status-text');
  const elLatency = document.getElementById('latency-display');
  const elStaleBanner = document.getElementById('stale-feed-banner');
  const elPaperBtn = document.getElementById('mode-paper-btn');
  const elLiveBtn = document.getElementById('mode-live-btn');
  const elKillSwitchBtn = document.getElementById('kill-switch-btn');
  const elTabCe = document.getElementById('btn-tab-ce');
  const elTabPe = document.getElementById('btn-tab-pe');
  const elTabCeLabel = document.getElementById('tab-ce-label');
  const elTabPeLabel = document.getElementById('tab-pe-label');
  const elSpotPrice = document.getElementById('meta-spot-price');
  const elOptionLtp = document.getElementById('meta-option-ltp');
  const elBestBid = document.getElementById('meta-best-bid');
  const elBestAsk = document.getElementById('meta-best-ask');
  const elLotSize = document.getElementById('meta-lot-size');
  const elDrawer = document.getElementById('trade-drawer');
  const elDrawerHeader = document.getElementById('drawer-header');
  const elDrawerToggleBtn = document.getElementById('drawer-toggle-btn');
  const elTradeCountBadge = document.getElementById('trade-count-badge');
  const elTradeTableBody = document.getElementById('trade-table-body');
  const elModal = document.getElementById('live-confirm-modal');
  const elModalInput = document.getElementById('live-confirm-input');
  const elModalCancel = document.getElementById('live-confirm-cancel');
  const elModalSubmit = document.getElementById('live-confirm-submit');

  // Initialize Chart
  function initChart() {
    const container = document.getElementById('chart-container');
    container.innerHTML = '';

    chart = LightweightCharts.createChart(container, {
      layout: {
        background: { type: 'solid', color: '#101522' },
        textColor: '#94a3b8',
        fontFamily: "'Inter', sans-serif"
      },
      grid: {
        vertLines: { color: 'rgba(255, 255, 255, 0.04)' },
        horzLines: { color: 'rgba(255, 255, 255, 0.04)' }
      },
      crosshair: {
        mode: LightweightCharts.CrosshairMode.Normal,
        vertLine: { color: '#38bdf8', width: 1, style: 3 },
        horzLine: { color: '#38bdf8', width: 1, style: 3 }
      },
      rightPriceScale: {
        borderColor: 'rgba(255, 255, 255, 0.08)',
        scaleMargins: { top: 0.1, bottom: 0.1 }
      },
      timeScale: {
        borderColor: 'rgba(255, 255, 255, 0.08)',
        timeVisible: true,
        secondsVisible: false
      }
    });

    candleSeries = chart.addCandlestickSeries({
      upColor: '#00e676',
      downColor: '#ff1744',
      borderUpColor: '#00e676',
      borderDownColor: '#ff1744',
      wickUpColor: '#00e676',
      wickDownColor: '#ff1744'
    });

    window.addEventListener('resize', () => {
      if (chart && container) {
        chart.applyOptions({
          width: container.clientWidth,
          height: container.clientHeight
        });
      }
    });
  }

  // Formatting helpers
  function formatINR(val) {
    const num = Number(val) || 0;
    const sign = num > 0 ? '+' : '';
    return `${sign}₹${num.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  }

  function updatePnlDisplay(realised, unrealised, total) {
    elRealisedPnl.textContent = formatINR(realised);
    elUnrealisedPnl.textContent = formatINR(unrealised);
    elTotalPnl.textContent = formatINR(total);

    elTotalPnl.className = 'pnl-value ' + (total > 0 ? 'profit' : total < 0 ? 'loss' : 'neutral');
    elRealisedPnl.className = 'pnl-value ' + (realised > 0 ? 'profit' : realised < 0 ? 'loss' : 'neutral');
    elUnrealisedPnl.className = 'pnl-value ' + (unrealised > 0 ? 'profit' : unrealised < 0 ? 'loss' : 'neutral');
  }

  // Load Initial Candles
  async function loadCandles(token) {
    activeToken = String(token);
    try {
      const resp = await fetch(`/api/candles?token=${token}`);
      const data = await resp.json();
      if (data && data.candles) {
        const chartData = data.candles.map(c => ({
          time: c.time,
          open: c.open,
          high: c.high,
          low: c.low,
          close: c.close
        }));
        candleSeries.setData(chartData);
        chart.timeScale().fitContent();
      }
    } catch (e) {
      console.error('Error loading candles:', e);
    }
  }

  // Active Trade Lines
  function updateActiveTradeLines(trade) {
    clearActiveTradeLines();
    if (!trade || String(trade.token) !== activeToken) return;

    entryLine = candleSeries.createPriceLine({
      price: trade.entry_price,
      color: '#ffeb3b',
      lineWidth: 2,
      lineStyle: LightweightCharts.LineStyle.Solid,
      axisLabelVisible: true,
      title: `ENTRY: ₹${trade.entry_price.toFixed(2)}`
    });

    stopLine = candleSeries.createPriceLine({
      price: trade.current_stop,
      color: '#ff1744',
      lineWidth: 2,
      lineStyle: LightweightCharts.LineStyle.Solid,
      axisLabelVisible: true,
      title: `SL: ₹${trade.current_stop.toFixed(2)}`
    });

    targetLine = candleSeries.createPriceLine({
      price: trade.target,
      color: '#00e5ff',
      lineWidth: 2,
      lineStyle: LightweightCharts.LineStyle.Solid,
      axisLabelVisible: true,
      title: `TARGET: ₹${trade.target.toFixed(2)}`
    });
  }

  function clearActiveTradeLines() {
    if (entryLine) { candleSeries.removePriceLine(entryLine); entryLine = null; }
    if (stopLine) { candleSeries.removePriceLine(stopLine); stopLine = null; }
    if (targetLine) { candleSeries.removePriceLine(targetLine); targetLine = null; }
  }

  // Marker Management
  function addChartMarker(time, position, shape, color, text) {
    markers.push({
      time: time,
      position: position,
      shape: shape,
      color: color,
      text: text
    });
    // Sort markers by time
    markers.sort((a, b) => a.time - b.time);
    candleSeries.setMarkers(markers);
  }

  // Fetch Status & Trades
  async function fetchStatus() {
    try {
      const resp = await fetch('/api/status');
      const data = await resp.json();

      contracts = data.contracts || {};
      if (contracts.atm_ce) {
        elTabCeLabel.textContent = contracts.atm_ce.symbol;
      }
      if (contracts.atm_pe) {
        elTabPeLabel.textContent = contracts.atm_pe.symbol;
      }

      // Mode
      if (data.execution_mode === 'LIVE') {
        elLiveBtn.className = 'mode-btn active live';
        elPaperBtn.className = 'mode-btn';
      } else {
        elPaperBtn.className = 'mode-btn active paper';
        elLiveBtn.className = 'mode-btn';
      }

      // Kill Switch
      if (data.is_kill_switch_active) {
        elKillSwitchBtn.classList.add('active');
        elKillSwitchBtn.innerHTML = '<span>KILL SWITCH (ACTIVE)</span>';
      } else {
        elKillSwitchBtn.classList.remove('active');
        elKillSwitchBtn.innerHTML = '<span>KILL SWITCH</span>';
      }

      // PnL
      const realised = (data.today_pnl && data.today_pnl.net_pnl) || 0;
      updatePnlDisplay(realised, 0, realised);

      // Active Trade
      if (data.active_trade) {
        updateActiveTradeLines(data.active_trade);
      }

      // Feed Status
      updateFeedHealth(data.feed_connected, data.feed_stale, data.avg_latency_ms);

    } catch (e) {
      console.error('Error fetching system status:', e);
    }
  }

  async function fetchTrades() {
    try {
      const resp = await fetch('/api/trades');
      const data = await resp.json();
      const trades = data.trades || [];
      elTradeCountBadge.textContent = `${trades.length} Trades`;

      if (trades.length === 0) {
        elTradeTableBody.innerHTML = `<tr><td colspan="10" style="text-align:center; color:var(--text-muted); padding:24px;">No closed trades yet today.</td></tr>`;
        return;
      }

      elTradeTableBody.innerHTML = trades.map(t => {
        const isWin = t.net_pnl > 0;
        const pnlClass = isWin ? 'style="color: var(--color-profit); font-weight:700;"' : 'style="color: var(--color-loss); font-weight:700;"';
        const timeStr = t.exit_time ? t.exit_time.split('T')[1].substring(0, 8) : '--';
        return `
          <tr>
            <td>${timeStr}</td>
            <td><strong>${t.symbol}</strong></td>
            <td><span class="strategy-badge">${t.option_type}</span></td>
            <td>${t.quantity}</td>
            <td>₹${t.entry_price.toFixed(2)}</td>
            <td>₹${t.exit_price ? t.exit_price.toFixed(2) : '--'}</td>
            <td>₹${t.gross_pnl.toFixed(2)}</td>
            <td>₹${t.charges.toFixed(2)}</td>
            <td ${pnlClass}>${formatINR(t.net_pnl)}</td>
            <td><span style="color:var(--text-secondary); font-size:11px;">${t.exit_reason || '--'}</span></td>
          </tr>
        `;
      }).join('');
    } catch (e) {
      console.error('Error fetching trades:', e);
    }
  }

  function updateFeedHealth(connected, stale, latency) {
    if (!connected || stale) {
      elFeedDot.className = 'status-dot stale';
      elFeedText.textContent = stale ? 'Feed Stale' : 'Disconnected';
      elStaleBanner.style.display = 'block';
    } else {
      elFeedDot.className = 'status-dot';
      elFeedText.textContent = 'Healthy';
      elStaleBanner.style.display = 'none';
    }
    elLatency.textContent = `(${latency || 0}ms)`;
  }

  // WebSocket Setup
  function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/stream`;

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      console.log('UI WebSocket connected.');
      updateFeedHealth(true, false, 0);
    };

    ws.onclose = () => {
      console.warn('UI WebSocket disconnected. Reconnecting in 3s...');
      updateFeedHealth(false, true, 0);
      setTimeout(connectWebSocket, 3000);
    };

    ws.onerror = (err) => {
      console.error('WebSocket error:', err);
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        handleWsMessage(msg);
      } catch (e) {
        console.error('Error handling WS message:', e);
      }
    };
  }

  function handleWsMessage(msg) {
    switch (msg.type) {
      case 'TICK':
        if (msg.token === activeToken) {
          elOptionLtp.textContent = `₹${msg.ltp.toFixed(2)}`;
          elBestBid.textContent = `₹${msg.best_bid.toFixed(2)}`;
          elBestAsk.textContent = `₹${msg.best_ask.toFixed(2)}`;
        }
        updatePnlDisplay(msg.realised_pnl, msg.unrealised_pnl, msg.total_pnl);
        updateFeedHealth(true, msg.is_feed_stale, msg.latency_ms);
        break;

      case 'CANDLE_UPDATE':
      case 'CANDLE_CLOSE':
        if (msg.token === activeToken && msg.candle) {
          candleSeries.update({
            time: msg.candle.time,
            open: msg.candle.open,
            high: msg.candle.high,
            low: msg.candle.low,
            close: msg.candle.close
          });
        }
        break;

      case 'SIGNAL':
        const sig = msg.signal;
        if (sig.token === activeToken) {
          const isCE = sig.contract_side === 'CE';
          const color = isCE ? '#00e676' : '#d946ef';
          const text = `BUY ${sig.contract_side} @ ${sig.entry_price}\nSL ${sig.stop_loss} | T ${sig.target}`;
          const timeVal = Math.floor(new Date(sig.timestamp).getTime() / 1000);
          addChartMarker(timeVal, 'belowBar', 'arrowUp', color, text);
        }
        break;

      case 'TRADE_EVENT':
        const evt = msg.event;
        const trade = msg.trade;
        if (evt === 'ENTRY') {
          updateActiveTradeLines(trade);
          const tVal = Math.floor(new Date(trade.entry_time).getTime() / 1000);
          addChartMarker(tVal, 'belowBar', 'circle', '#ffeb3b', `BOT FILL: BUY @ ₹${trade.entry_price.toFixed(2)}`);
        } else if (evt === 'TRAIL_STOP') {
          if (stopLine && trade.current_stop) {
            candleSeries.removePriceLine(stopLine);
            stopLine = candleSeries.createPriceLine({
              price: trade.current_stop,
              color: '#ff1744',
              lineWidth: 2,
              lineStyle: LightweightCharts.LineStyle.Solid,
              title: `SL (TRAIL): ₹${trade.current_stop.toFixed(2)}`
            });
          }
        } else if (evt === 'EXIT') {
          clearActiveTradeLines();
          const tVal = Math.floor(new Date(trade.exit_time).getTime() / 1000);
          const exitColor = trade.exit_reason.includes('TARGET') ? '#00e5ff' : (trade.exit_reason.includes('SL') ? '#ff1744' : '#94a3b8');
          addChartMarker(tVal, 'aboveBar', 'circle', exitColor, `BOT FILL: SELL @ ₹${trade.exit_price.toFixed(2)}\n[${trade.exit_reason}]`);
          fetchTrades();
        }
        break;

      case 'MODE_CHANGE':
        if (msg.mode === 'LIVE') {
          elLiveBtn.className = 'mode-btn active live';
          elPaperBtn.className = 'mode-btn';
        } else {
          elPaperBtn.className = 'mode-btn active paper';
          elLiveBtn.className = 'mode-btn';
        }
        break;

      case 'KILL_SWITCH':
        if (msg.active) {
          elKillSwitchBtn.classList.add('active');
          elKillSwitchBtn.innerHTML = '<span>KILL SWITCH (ACTIVE)</span>';
        } else {
          elKillSwitchBtn.classList.remove('active');
          elKillSwitchBtn.innerHTML = '<span>KILL SWITCH</span>';
        }
        break;
    }
  }

  // Event Listeners
  elTabCe.addEventListener('click', () => {
    if (contracts.atm_ce && activeToken !== String(contracts.atm_ce.token)) {
      elTabCe.className = 'tab-btn ce active';
      elTabPe.className = 'tab-btn pe';
      markers = [];
      clearActiveTradeLines();
      loadCandles(contracts.atm_ce.token);
    }
  });

  elTabPe.addEventListener('click', () => {
    if (contracts.atm_pe && activeToken !== String(contracts.atm_pe.token)) {
      elTabPe.className = 'tab-btn pe active';
      elTabCe.className = 'tab-btn ce';
      markers = [];
      clearActiveTradeLines();
      loadCandles(contracts.atm_pe.token);
    }
  });

  // Mode Toggle Events
  elPaperBtn.addEventListener('click', async () => {
    try {
      const resp = await fetch('/api/mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: 'PAPER' })
      });
      const data = await resp.json();
      if (!resp.ok) alert(data.detail || 'Could not switch mode');
    } catch (e) {
      alert('Error switching to PAPER mode: ' + e);
    }
  });

  elLiveBtn.addEventListener('click', () => {
    elModalInput.value = '';
    elModal.classList.add('active');
    elModalInput.focus();
  });

  elModalCancel.addEventListener('click', () => {
    elModal.classList.remove('active');
  });

  elModalSubmit.addEventListener('click', async () => {
    const text = elModalInput.value.trim();
    if (text !== 'LIVE') {
      alert('You must type LIVE exactly to switch to LIVE mode.');
      return;
    }
    try {
      const resp = await fetch('/api/mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: 'LIVE', confirmation: 'LIVE' })
      });
      const data = await resp.json();
      if (!resp.ok) {
        alert(data.detail || 'Could not switch to LIVE mode');
      } else {
        elModal.classList.remove('active');
      }
    } catch (e) {
      alert('Error switching to LIVE mode: ' + e);
    }
  });

  // Kill Switch
  elKillSwitchBtn.addEventListener('click', async () => {
    const isActivating = !elKillSwitchBtn.classList.contains('active');
    const msg = isActivating
      ? 'EMERGENCY: Are you sure you want to trigger the KILL SWITCH? This will square off any open position immediately and halt trading!'
      : 'Deactivate the emergency kill switch and resume normal trading operations?';
    if (!confirm(msg)) return;

    try {
      const resp = await fetch('/api/kill-switch', { method: 'POST' });
      const data = await resp.json();
      if (!resp.ok) alert(data.detail || 'Error toggling kill switch');
    } catch (e) {
      alert('Error triggering kill switch: ' + e);
    }
  });

  // Drawer Toggle
  function toggleDrawer() {
    elDrawer.classList.toggle('open');
    const isOpen = elDrawer.classList.contains('open');
    document.getElementById('drawer-arrow').innerHTML = isOpen ? '&#9660;' : '&#9650;';
    if (isOpen) fetchTrades();
  }
  elDrawerHeader.addEventListener('click', toggleDrawer);
  elDrawerToggleBtn.addEventListener('click', toggleDrawer);

  // App Initialization
  async function init() {
    initChart();
    await fetchStatus();
    if (contracts.atm_ce) {
      loadCandles(contracts.atm_ce.token);
    }
    fetchTrades();
    connectWebSocket();
  }

  window.addEventListener('DOMContentLoaded', init);
})();
