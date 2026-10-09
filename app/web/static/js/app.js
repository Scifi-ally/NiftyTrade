/**
 * NiftyTrades - Borderless & Box-Free Client Engine
 * Pure white background, black font, borderless scales, compact markers,
 * and live Angel One SmartAPI tick updates.
 */

(function () {
  'use strict';

  // State
  let chart = null;
  let candleSeries = null;
  let activeToken = null;
  let contracts = {};
  let ws = null;
  let markers = [];
  let markerMapByTime = new Map();
  let entryLine = null;
  let stopLine = null;
  let targetLine = null;
  let allTrades = [];
  let activeTrade = null;
  let currentCandles = [];

  // DOM Elements
  const elTotalPnl = document.getElementById('total-pnl');
  const elCapital = document.getElementById('meta-capital');
  const elPnlCardContainer = document.getElementById('pnl-card-container');
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
  const elSignalInspector = document.getElementById('signal-inspector');
  const elInspectorText = document.getElementById('inspector-text');
  const elBtnLabelShapes = document.getElementById('btn-label-shapes');
  const elBtnLabelCompact = document.getElementById('btn-label-compact');
  const elBtnLabelFull = document.getElementById('btn-label-full');
  const elChartTooltip = document.getElementById('chart-tooltip');
  const elDrawer = document.getElementById('trade-drawer');
  const elDrawerToggleBtn = document.getElementById('drawer-toggle-btn');
  const elDrawerCloseBtn = document.getElementById('drawer-close-btn');
  const elTradeCountBadge = document.getElementById('trade-count-badge');
  const elTradeTableBody = document.getElementById('trade-table-body');
  const elModal = document.getElementById('live-confirm-modal');
  const elModalInput = document.getElementById('live-confirm-input');
  const elModalCancel = document.getElementById('live-confirm-cancel');
  const elModalSubmit = document.getElementById('live-confirm-submit');

  // Sidebar Elements
  const elSidebar = document.getElementById('trades-sidebar');
  const elSidebarToggleBtn = document.getElementById('sidebar-toggle-btn');
  const elSidebarCloseBtn = document.getElementById('sidebar-close-btn');
  const elSidebarCount = document.getElementById('sidebar-count');
  const elSidebarTradesList = document.getElementById('sidebar-trades-list');
  const elSidebarToggleBadge = document.getElementById('sidebar-toggle-badge');

  let currentLabelMode = localStorage.getItem('nifty_label_mode') || 'full'; // 'shapes' | 'compact' | 'full'
  let startingCapital = 100000.0;
  let latestCandle = null;

  function showCandleInInspector(c) {
    if (!elInspectorText || !c) return;
    const d = new Date(c.time * 1000);
    const hh = String(d.getUTCHours()).padStart(2, '0');
    const mm = String(d.getUTCMinutes()).padStart(2, '0');
    const timeStr = `${hh}:${mm} IST`;
    const isUp = c.close >= c.open;
    const cColor = isUp ? 'var(--color-green)' : 'var(--color-red)';
    elInspectorText.innerHTML = `<span style="color:#64748b; font-weight:600;">${timeStr}</span> &nbsp;&bull;&nbsp; O:<strong>₹${c.open.toFixed(2)}</strong> H:<strong>₹${c.high.toFixed(2)}</strong> L:<strong>₹${c.low.toFixed(2)}</strong> C:<strong style="color:${cColor};">₹${c.close.toFixed(2)}</strong>`;
  }

  function showLatestCandleInInspector() {
    if (!elInspectorText) return;
    if (!latestCandle) {
      elInspectorText.innerHTML = '';
      return;
    }
    showCandleInInspector(latestCandle);
  }

  function toggleSidebar(forceOpen) {
    if (!elSidebar) return;
    if (typeof forceOpen === 'boolean') {
      elSidebar.classList.toggle('collapsed', !forceOpen);
    } else {
      elSidebar.classList.toggle('collapsed');
    }
    setTimeout(() => {
      if (chart) {
        const container = document.getElementById('chart-container');
        if (container) {
          chart.resize(container.clientWidth, container.clientHeight);
        }
      }
    }, 240);
  }

  // Initialize Minimalist White Borderless Chart
  function initChart() {
    const container = document.getElementById('chart-container');
    if (!container) return;
    container.innerHTML = '';

    const width = container.clientWidth || 800;
    const height = container.clientHeight || 500;

    chart = LightweightCharts.createChart(container, {
      width: width,
      height: height,
      layout: {
        background: { type: 'solid', color: '#ffffff' },
        textColor: '#000000',
        fontFamily: "'Geist Mono Variable', 'Geist Mono', ui-monospace, SFMono-Regular, monospace"
      },
      grid: {
        vertLines: { color: '#f8fafc' },
        horzLines: { color: '#f8fafc' }
      },
      crosshair: {
        mode: LightweightCharts.CrosshairMode.Normal,
        vertLine: { color: '#94a3b8', width: 1, style: 2 },
        horzLine: { color: '#94a3b8', width: 1, style: 2 }
      },
      rightPriceScale: {
        borderColor: 'transparent',
        textColor: '#000000',
        scaleMargins: { top: 0.12, bottom: 0.12 }
      },
      timeScale: {
        borderColor: 'transparent',
        textColor: '#000000',
        timeVisible: true,
        secondsVisible: false,
        barSpacing: 10,
        minBarSpacing: 3,
        rightOffset: 12
      },
      localization: {
        locale: 'en-IN',
        dateFormat: 'dd MMM yyyy'
      }
    });

    candleSeries = chart.addCandlestickSeries({
      upColor: '#089981',
      downColor: '#ef4444',
      borderUpColor: '#089981',
      borderDownColor: '#ef4444',
      wickUpColor: '#089981',
      wickDownColor: '#ef4444'
    });

    // Crosshair inspection on flat text display & floating marker tooltip
    chart.subscribeCrosshairMove(param => {
      if (!param || !param.time || !param.seriesPrices) {
        showLatestCandleInInspector();
        if (elChartTooltip) elChartTooltip.style.display = 'none';
        return;
      }

      const price = param.seriesPrices.get(candleSeries);
      const markerList = markerMapByTime.get(param.time);

      // Format time string (param.time already has IST alignment)
      const d = new Date(param.time * 1000);
      const hh = String(d.getUTCHours()).padStart(2, '0');
      const mm = String(d.getUTCMinutes()).padStart(2, '0');
      const timeStr = `${hh}:${mm} IST`;

      if (markerList && markerList.length > 0) {
        const detailsStr = markerList.map(m => `<span style="color:#000000; font-weight:800;">[${m.text || (m.shape === 'circle' ? 'BOT ORDER' : 'SIGNAL')}]:</span> ${m.details || ''}`).join(' &nbsp;|&nbsp; ');
        if (elInspectorText) {
          elInspectorText.innerHTML = `<span style="color:#64748b; font-weight:700;">${timeStr}</span> &nbsp;&bull;&nbsp; ${detailsStr}`;
        }

        // Show and position floating tooltip near cursor
        if (elChartTooltip && param.point) {
          const containerRect = container.getBoundingClientRect();
          let html = `<div class="tt-time">${timeStr}</div>`;
          for (const m of markerList) {
            const badgeColor = m.color || '#000000';
            const title = m.text || (m.shape === 'circle' ? 'BOT ORDER' : 'SIGNAL');
            html += `
              <div class="tt-item">
                <div class="tt-title" style="color:${badgeColor};">${title}</div>
                <div class="tt-desc">${m.details || ''}</div>
              </div>
            `;
          }
          elChartTooltip.innerHTML = html;
          elChartTooltip.style.display = 'block';

          const ttWidth = elChartTooltip.offsetWidth || 200;
          const ttHeight = elChartTooltip.offsetHeight || 80;
          let leftPos = param.point.x + 18;
          if (leftPos + ttWidth > containerRect.width - 20) {
            leftPos = param.point.x - ttWidth - 18;
          }
          let topPos = Math.max(10, param.point.y - 30);
          if (topPos + ttHeight > containerRect.height - 20) {
            topPos = containerRect.height - ttHeight - 20;
          }
          elChartTooltip.style.left = `${Math.max(10, leftPos)}px`;
          elChartTooltip.style.top = `${Math.max(10, topPos)}px`;
        }
      } else {
        if (elChartTooltip) elChartTooltip.style.display = 'none';
        if (price && elInspectorText) {
          elInspectorText.innerHTML = `<span style="color:#64748b; font-weight:700;">${timeStr}</span> &nbsp;&bull;&nbsp; O: <strong>₹${price.open.toFixed(2)}</strong> &nbsp;|&nbsp; H: <strong>₹${price.high.toFixed(2)}</strong> &nbsp;|&nbsp; L: <strong>₹${price.low.toFixed(2)}</strong> &nbsp;|&nbsp; C: <strong>₹${price.close.toFixed(2)}</strong>`;
        }
      }
    });

    container.addEventListener('mouseleave', () => {
      if (elChartTooltip) elChartTooltip.style.display = 'none';
      showLatestCandleInInspector();
    });

    // ResizeObserver cleanly resizes inside container without blowout
    const resizeObserver = new ResizeObserver(entries => {
      if (!entries || !entries.length) return;
      const { width, height } = entries[0].contentRect;
      if (chart && width > 0 && height > 0) {
        chart.applyOptions({ width: Math.floor(width), height: Math.floor(height) });
      }
    });
    resizeObserver.observe(container);
  }

  // Formatting helpers
  function formatINR(val) {
    const num = Number(val) || 0;
    const sign = num > 0 ? '+' : '';
    return `${sign}₹${num.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  }

  function updatePnlDisplay(total) {
    const num = Number(total) || 0;
    if (elTotalPnl) {
      elTotalPnl.textContent = formatINR(num);
      elTotalPnl.className = num > 0 ? 'm-val mono profit' : num < 0 ? 'm-val mono loss' : 'm-val mono neutral';
    }
    if (elPnlCardContainer) {
      elPnlCardContainer.className = num > 0 ? 'metric-card pnl-card profit' : num < 0 ? 'metric-card pnl-card loss' : 'metric-card pnl-card';
    }
    if (elCapital) {
      const currentCapital = Math.max(0, startingCapital + num);
      elCapital.textContent = `₹${currentCapital.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    }
  }

  // Active Trade Lines (Entry, Stop Loss, Target)
  function updateActiveTradeLines(trade) {
    clearActiveTradeLines();
    if (!trade || String(trade.token) !== activeToken) return;

    entryLine = candleSeries.createPriceLine({
      price: trade.entry_price,
      color: '#d97706',
      lineWidth: 2,
      lineStyle: LightweightCharts.LineStyle.Solid,
      axisLabelVisible: true,
      title: `ENTRY: ₹${trade.entry_price.toFixed(2)}`
    });

    stopLine = candleSeries.createPriceLine({
      price: trade.current_stop,
      color: '#ef4444',
      lineWidth: 2,
      lineStyle: LightweightCharts.LineStyle.Solid,
      axisLabelVisible: true,
      title: `SL: ₹${trade.current_stop.toFixed(2)}`
    });

    targetLine = candleSeries.createPriceLine({
      price: trade.target,
      color: '#0891b2',
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

  const IST_OFFSET_SECONDS = 19800;

  function toChartTime(val) {
    if (!val) return Math.floor(Date.now() / 1000) + IST_OFFSET_SECONDS;
    let epochSec = 0;
    if (typeof val === 'number') {
      epochSec = val > 1e11 ? Math.floor(val / 1000) : Math.floor(val);
    } else {
      epochSec = Math.floor(new Date(val).getTime() / 1000);
    }
    // Snap to 1-minute candle bucket (60s)
    const snapped = Math.floor(epochSec / 60) * 60;
    return snapped + IST_OFFSET_SECONDS;
  }

  // Helper to extract clean compact tags
  function toCompactBadge(text) {
    if (!text) return '';
    if (text.includes('BOT BUY')) return 'B';
    if (text.includes('BOT SOLD')) return 'S';
    if (text.includes('BUY CE')) return 'CE';
    if (text.includes('BUY PE')) return 'PE';
    if (text.includes('SL')) return 'SL';
    if (text.includes('TARGET')) return 'TP';
    if (text.includes('TREND')) return 'TR';
    if (text.includes('TIME')) return 'TM';
    if (text.includes('HOLD')) return 'HD';
    if (text.includes('BUY')) return 'B';
    if (text.includes('SELL') || text.includes('EXIT')) return 'X';
    return text.substring(0, 3).toUpperCase();
  }

  // Format markers to prevent canvas label collisions
  function formatMarkersForDisplay(rawList, mode) {
    if (!rawList || rawList.length === 0) return [];

    // Sort chronologically
    const sorted = [...rawList].sort((a, b) => a.time - b.time);

    // Consolidate markers on the exact same bar & position
    const barMap = new Map();
    for (const m of sorted) {
      const key = `${m.time}_${m.position}`;
      if (!barMap.has(key)) {
        barMap.set(key, []);
      }
      barMap.get(key).push(m);
    }

    const consolidated = [];
    for (const [key, items] of barMap.entries()) {
      if (items.length === 1) {
        consolidated.push({ ...items[0] });
      } else {
        // Prioritize bot execution fill if present (black circle)
        const botItem = items.find(x => x.shape === 'circle' || (x.text && x.text.includes('BOT')));
        const primary = botItem || items[0];
        consolidated.push({
          time: primary.time,
          position: primary.position,
          shape: primary.shape,
          color: primary.color,
          text: primary.text,
          details: items.map(x => x.details || x.text).join(' | ')
        });
      }
    }

    consolidated.sort((a, b) => a.time - b.time);

    // Always show full labels on the chart at all times with crisp pill backgrounds
    return consolidated.map(m => ({
      time: m.time,
      position: m.position,
      shape: m.shape,
      color: m.color,
      size: m.shape === 'circle' ? 2 : 1,
      text: m.text || ''
    }));
  }

  function updateMarkersDisplay() {
    markerMapByTime.clear();
    for (const m of markers) {
      if (!markerMapByTime.has(m.time)) {
        markerMapByTime.set(m.time, []);
      }
      markerMapByTime.get(m.time).push(m);
    }
    markers.sort((a, b) => a.time - b.time);

    if (candleSeries) {
      const displayMarkers = formatMarkersForDisplay(markers);
      candleSeries.setMarkers(displayMarkers);
    }
  }

  // Markers
  function setChartMarkers(newMarkers) {
    markers = newMarkers || [];
    updateMarkersDisplay();
  }

  function addChartMarker(markerObj) {
    markers.push(markerObj);
    updateMarkersDisplay();
  }

  // Render Monitored Trades in Collapsible Right Sidebar
  function renderSidebarTrades() {
    if (!elSidebarTradesList) return;

    const list = [];
    if (activeTrade) {
      list.push({ ...activeTrade, is_active: true });
    }
    if (allTrades && allTrades.length > 0) {
      list.push(...allTrades);
    }

    const count = list.length;
    if (elSidebarCount) elSidebarCount.textContent = count;
    if (elSidebarToggleBadge) elSidebarToggleBadge.textContent = count;
    if (elTradeCountBadge) elTradeCountBadge.textContent = count;

    if (count === 0) {
      elSidebarTradesList.innerHTML = `<div class="no-trades-msg">No trades monitored today yet.</div>`;
      return;
    }

    elSidebarTradesList.innerHTML = list.map((t, idx) => {
      const isWin = t.net_pnl > 0;
      const isLoss = t.net_pnl < 0;
      const pnlClass = isWin ? 'profit' : (isLoss ? 'loss' : 'neutral');
      const pnlFormatted = formatINR(t.net_pnl);
      const optType = t.option_type || (t.symbol && t.symbol.endsWith('PE') ? 'PE' : 'CE');
      const timeVal = t.entry_time ? (t.entry_time.includes('T') ? t.entry_time.split('T')[1].substring(0, 5) : t.entry_time) : '--:--';
      const tradeId = t.id || `trade_${idx}`;

      return `
        <div class="trade-card" data-trade-id="${tradeId}">
          <div class="trade-card-top">
            <span class="trade-side-badge ${optType.toLowerCase()}">${optType}</span>
            <span class="trade-time mono">${timeVal} IST</span>
            ${t.is_active ? '<span class="trade-live-badge">ACTIVE</span>' : ''}
          </div>
          <div class="trade-card-pnl ${pnlClass}">
            <span class="pnl-lbl">NET P&amp;L</span>
            <span class="pnl-amount">${pnlFormatted}</span>
          </div>
        </div>
      `;
    }).join('');

    // Bind click listener: Jump to chart and center on trade point
    elSidebarTradesList.querySelectorAll('.trade-card').forEach((cardEl, idx) => {
      cardEl.addEventListener('click', () => {
        const tradeObj = list[idx];
        focusTradeOnChart(tradeObj, cardEl);
      });
    });
  }

  // Focus specific trade on chart
  async function focusTradeOnChart(trade, cardEl) {
    if (!trade) return;

    // Highlight selected card
    if (elSidebarTradesList) {
      elSidebarTradesList.querySelectorAll('.trade-card').forEach(c => c.classList.remove('selected'));
    }
    if (cardEl) cardEl.classList.add('selected');

    // Switch contract if trade belongs to other contract
    const tradeToken = String(trade.token || '');
    if (tradeToken && tradeToken !== activeToken) {
      if (contracts.atm_ce && String(contracts.atm_ce.token) === tradeToken) {
        if (elTabCe) elTabCe.className = 'contract-pill active';
        if (elTabPe) elTabPe.className = 'contract-pill';
      } else if (contracts.atm_pe && String(contracts.atm_pe.token) === tradeToken) {
        if (elTabPe) elTabPe.className = 'contract-pill active';
        if (elTabCe) elTabCe.className = 'contract-pill';
      } else {
        if (trade.option_type === 'CE' || (trade.symbol && trade.symbol.endsWith('CE'))) {
          if (elTabCe) elTabCe.className = 'contract-pill active';
          if (elTabPe) elTabPe.className = 'contract-pill';
        } else if (trade.option_type === 'PE' || (trade.symbol && trade.symbol.endsWith('PE'))) {
          if (elTabPe) elTabPe.className = 'contract-pill active';
          if (elTabCe) elTabCe.className = 'contract-pill';
        }
      }
      clearActiveTradeLines();
      await loadCandles(tradeToken);
    }

    if (trade.is_active) {
      updateActiveTradeLines(trade);
    }

    // Scroll chart to exact trade timestamp
    const targetTime = trade.chart_time || toChartTime(trade.entry_time || trade.exit_time);
    if (currentCandles && currentCandles.length > 0) {
      let bestIdx = -1;
      let minDiff = Infinity;
      for (let i = 0; i < currentCandles.length; i++) {
        const diff = Math.abs(currentCandles[i].time - targetTime);
        if (diff < minDiff) {
          minDiff = diff;
          bestIdx = i;
        }
      }
      if (bestIdx !== -1) {
        chart.timeScale().setVisibleLogicalRange({
          from: Math.max(0, bestIdx - 15),
          to: Math.min(currentCandles.length + 5, bestIdx + 15)
        });
        showCandleInInspector(currentCandles[bestIdx]);
      }
    }
  }

  // Load Candles & Historical Markers
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
        currentCandles = chartData;
        candleSeries.setData(chartData);

        // Display recent ~90 bars so candles are clearly visible with proper width
        if (chartData.length > 0) {
          latestCandle = chartData[chartData.length - 1];
          showLatestCandleInInspector();
          const count = chartData.length;
          const visibleBars = Math.min(count, 90);
          chart.timeScale().setVisibleLogicalRange({
            from: count - visibleBars,
            to: count + 8
          });
        }

        // Render historical indicator signals & bot execution fills
        if (data.markers && Array.isArray(data.markers)) {
          setChartMarkers(data.markers);
        } else {
          setChartMarkers([]);
        }
      }
    } catch (e) {
      console.error('Error loading candles:', e);
    }
  }

  // Fetch Status
  async function fetchStatus() {
    try {
      const resp = await fetch('/api/status');
      const data = await resp.json();

      contracts = data.contracts || {};
      if (contracts.atm_ce && elTabCeLabel) {
        const strikeText = contracts.atm_ce.strike ? `${Math.round(contracts.atm_ce.strike)} CE` : 'ATM CE';
        elTabCeLabel.textContent = strikeText;
        if (elTabCe) elTabCe.title = contracts.atm_ce.symbol || strikeText;
      }
      if (contracts.atm_pe && elTabPeLabel) {
        const strikeText = contracts.atm_pe.strike ? `${Math.round(contracts.atm_pe.strike)} PE` : 'ATM PE';
        elTabPeLabel.textContent = strikeText;
        if (elTabPe) elTabPe.title = contracts.atm_pe.symbol || strikeText;
      }
      if (contracts.spot && elSpotPrice) {
        elSpotPrice.textContent = `₹${contracts.spot.ltp ? contracts.spot.ltp.toFixed(2) : '--'}`;
      }

      // Mode
      if (data.execution_mode === 'LIVE') {
        elLiveBtn.className = 'mode-pill active live';
        elPaperBtn.className = 'mode-pill';
      } else {
        elPaperBtn.className = 'mode-pill active paper';
        elLiveBtn.className = 'mode-pill';
      }

      // Kill Switch
      if (data.is_kill_switch_active) {
        elKillSwitchBtn.classList.add('active');
        elKillSwitchBtn.textContent = 'KILL (ACTIVE)';
      } else {
        elKillSwitchBtn.classList.remove('active');
        elKillSwitchBtn.textContent = 'KILL SWITCH';
      }

      // Starting Capital (Paper Trading 1 Lakh)
      if (typeof data.funds === 'number' && data.funds > 0) {
        startingCapital = data.funds;
      }

      // PnL
      const netPnl = (data.today_pnl && data.today_pnl.net_pnl) || 0;
      updatePnlDisplay(netPnl);

      // Active Trade
      if (data.active_trade) {
        activeTrade = data.active_trade;
        updateActiveTradeLines(data.active_trade);
      } else {
        activeTrade = null;
      }
      renderSidebarTrades();

      // Feed Status
      updateFeedHealth(data.feed_connected, data.feed_stale, data.avg_latency_ms);

    } catch (e) {
      console.error('Error fetching system status:', e);
    }
  }

  // Fetch Closed Trades
  async function fetchTrades() {
    try {
      const resp = await fetch('/api/trades');
      const data = await resp.json();
      allTrades = data.trades || [];
      renderSidebarTrades();

      if (elTradeCountBadge) {
        elTradeCountBadge.textContent = allTrades.length;
      }

      if (!elTradeTableBody) return;

      if (allTrades.length === 0) {
        elTradeTableBody.innerHTML = `<tr><td colspan="10" style="text-align:center; color:#64748b; padding:24px;">No closed trades yet today.</td></tr>`;
        return;
      }

      elTradeTableBody.innerHTML = allTrades.map(t => {
        const isWin = t.net_pnl > 0;
        const pnlStyle = isWin ? 'style="color: var(--color-green); font-weight:800;"' : 'style="color: var(--color-red); font-weight:800;"';
        const timeStr = t.exit_time ? t.exit_time.split('T')[1].substring(0, 8) : '--';
        return `
          <tr>
            <td>${timeStr}</td>
            <td><strong>${t.symbol}</strong></td>
            <td>${t.option_type}</td>
            <td>${t.quantity}</td>
            <td>₹${t.entry_price.toFixed(2)}</td>
            <td>₹${t.exit_price ? t.exit_price.toFixed(2) : '--'}</td>
            <td>₹${t.gross_pnl.toFixed(2)}</td>
            <td>₹${t.charges.toFixed(2)}</td>
            <td ${pnlStyle}>${formatINR(t.net_pnl)}</td>
            <td><span style="color:#64748b; font-size:11px;">${t.exit_reason || '--'}</span></td>
          </tr>
        `;
      }).join('');
    } catch (e) {
      console.error('Error fetching trades:', e);
    }
  }

  function updateFeedHealth(connected, stale, latency) {
    if (!elFeedDot) return;
    if (!connected || stale) {
      elFeedDot.className = 'status-dot stale';
      elFeedText.textContent = stale ? 'Feed Stale' : 'Disconnected';
      if (elStaleBanner) elStaleBanner.style.display = 'block';
    } else {
      elFeedDot.className = 'status-dot';
      elFeedText.textContent = 'Connected';
      if (elStaleBanner) elStaleBanner.style.display = 'none';
    }
    if (elLatency) elLatency.textContent = `(${latency || 0}ms)`;
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
          if (elOptionLtp) elOptionLtp.textContent = `₹${msg.ltp.toFixed(2)}`;
          if (elBestBid) elBestBid.textContent = `₹${msg.best_bid.toFixed(2)}`;
          if (elBestAsk) elBestAsk.textContent = `₹${msg.best_ask.toFixed(2)}`;
        }
        if (contracts.spot && String(contracts.spot.token) === msg.token && elSpotPrice) {
          elSpotPrice.textContent = `₹${msg.ltp.toFixed(2)}`;
        }
        updatePnlDisplay(msg.total_pnl);
        updateFeedHealth(true, msg.is_feed_stale, msg.latency_ms);

        // Update active trade card Net P&L in sidebar dynamically
        if (activeTrade && String(activeTrade.token) === String(msg.token)) {
          if (typeof msg.unrealised_pnl === 'number') {
            activeTrade.net_pnl = msg.unrealised_pnl;
            const liveBadge = elSidebarTradesList ? elSidebarTradesList.querySelector('.trade-live-badge') : null;
            if (liveBadge) {
              const cardPnl = liveBadge.closest('.trade-card')?.querySelector('.trade-card-pnl');
              if (cardPnl) {
                const isWin = msg.unrealised_pnl > 0;
                const isLoss = msg.unrealised_pnl < 0;
                cardPnl.className = `trade-card-pnl ${isWin ? 'profit' : (isLoss ? 'loss' : 'neutral')}`;
                const amt = cardPnl.querySelector('.pnl-amount');
                if (amt) amt.textContent = formatINR(msg.unrealised_pnl);
              }
            }
          }
        }
        break;

      case 'CANDLE_UPDATE':
      case 'CANDLE_CLOSE':
        if (msg.token === activeToken && msg.candle) {
          latestCandle = msg.candle;
          candleSeries.update({
            time: msg.candle.time,
            open: msg.candle.open,
            high: msg.candle.high,
            low: msg.candle.low,
            close: msg.candle.close
          });
          showLatestCandleInInspector();
        }
        break;

      case 'SIGNAL':
        const sig = msg.signal;
        if (sig.token === activeToken) {
          const timeVal = sig.chart_time || toChartTime(sig.timestamp);
          if (sig.action === 'SELL') {
            const reason = sig.reason || '';
            const color = (reason.includes('SL') || reason.includes('STOP')) ? '#ef4444' : (reason.includes('TARGET') ? '#0891b2' : '#64748b');
            const shortText = reason.includes('SL') ? 'SL EXIT' : (reason.includes('TARGET') ? 'TARGET' : 'TIME EXIT');
            addChartMarker({
              time: timeVal,
              position: 'aboveBar',
              shape: 'arrowDown',
              color: color,
              text: shortText,
              details: `SELL ${sig.contract_side || 'OPT'} (${reason}) @ ₹${sig.exit_price ? sig.exit_price.toFixed(2) : (sig.entry_price || '--')}`
            });
          } else {
            const isCE = sig.contract_side === 'CE';
            const color = isCE ? '#089981' : '#c026d3';
            addChartMarker({
              time: timeVal,
              position: 'belowBar',
              shape: 'arrowUp',
              color: color,
              text: `BUY ${sig.contract_side || 'OPT'}`,
              details: `BUY ${sig.contract_side} @ ₹${sig.entry_price} | SL: ₹${sig.stop_loss} | Target: ₹${sig.target}`
            });
          }
        }
        break;

      case 'TRADE_EVENT':
        const evt = msg.event;
        const trade = msg.trade;
        if (evt === 'ENTRY') {
          activeTrade = { ...trade, is_active: true };
          renderSidebarTrades();
          updateActiveTradeLines(trade);
          const tVal = trade.chart_time || toChartTime(trade.entry_time);
          addChartMarker({
            time: tVal,
            position: 'belowBar',
            shape: 'circle',
            color: '#000000',
            text: 'BOT BUY',
            details: `BOT BOUGHT @ ₹${trade.entry_price.toFixed(2)} (Qty: ${trade.quantity})`
          });
        } else if (evt === 'TRAIL_STOP') {
          if (activeTrade) {
            activeTrade.current_stop = trade.current_stop;
          }
          if (stopLine && trade.current_stop) {
            candleSeries.removePriceLine(stopLine);
            stopLine = candleSeries.createPriceLine({
              price: trade.current_stop,
              color: '#ef4444',
              lineWidth: 2,
              lineStyle: LightweightCharts.LineStyle.Solid,
              title: `SL (TRAIL): ₹${trade.current_stop.toFixed(2)}`
            });
          }
        } else if (evt === 'EXIT') {
          activeTrade = null;
          clearActiveTradeLines();
          const tVal = trade.chart_time || toChartTime(trade.exit_time);
          const pnlStr = trade.net_pnl >= 0 ? `+₹${trade.net_pnl.toFixed(2)}` : `-₹${Math.abs(trade.net_pnl).toFixed(2)}`;
          addChartMarker({
            time: tVal,
            position: 'aboveBar',
            shape: 'circle',
            color: '#000000',
            text: 'BOT SOLD',
            details: `BOT SOLD @ ₹${trade.exit_price ? trade.exit_price.toFixed(2) : '--'} (Net: ${pnlStr})`
          });
          fetchTrades();
        }
        break;

      case 'CONTRACTS_UPDATE':
        contracts = msg.contracts || {};
        if (contracts.atm_ce && elTabCeLabel) {
          const strikeText = contracts.atm_ce.strike ? `${Math.round(contracts.atm_ce.strike)} CE` : (contracts.atm_ce.symbol || 'ATM CE');
          elTabCeLabel.textContent = strikeText;
          if (elTabCe) elTabCe.title = contracts.atm_ce.symbol || strikeText;
        }
        if (contracts.atm_pe && elTabPeLabel) {
          const strikeText = contracts.atm_pe.strike ? `${Math.round(contracts.atm_pe.strike)} PE` : (contracts.atm_pe.symbol || 'ATM PE');
          elTabPeLabel.textContent = strikeText;
          if (elTabPe) elTabPe.title = contracts.atm_pe.symbol || strikeText;
        }
        break;

      case 'MODE_CHANGE':
        if (msg.mode === 'LIVE') {
          elLiveBtn.className = 'mode-pill active live';
          elPaperBtn.className = 'mode-pill';
        } else {
          elPaperBtn.className = 'mode-pill active paper';
          elLiveBtn.className = 'mode-pill';
        }
        break;

      case 'KILL_SWITCH':
        if (msg.active) {
          elKillSwitchBtn.classList.add('active');
          elKillSwitchBtn.textContent = 'KILL (ACTIVE)';
        } else {
          elKillSwitchBtn.classList.remove('active');
          elKillSwitchBtn.textContent = 'KILL SWITCH';
        }
        break;
    }
  }

  // Contract Switch Event Listeners
  if (elTabCe) {
    elTabCe.addEventListener('click', () => {
      if (contracts.atm_ce && activeToken !== String(contracts.atm_ce.token)) {
        elTabCe.className = 'contract-pill active';
        if (elTabPe) elTabPe.className = 'contract-pill';
        clearActiveTradeLines();
        loadCandles(contracts.atm_ce.token);
      }
    });
  }

  if (elTabPe) {
    elTabPe.addEventListener('click', () => {
      if (contracts.atm_pe && activeToken !== String(contracts.atm_pe.token)) {
        elTabPe.className = 'contract-pill active';
        if (elTabCe) elTabCe.className = 'contract-pill';
        clearActiveTradeLines();
        loadCandles(contracts.atm_pe.token);
      }
    });
  }

  // Trades Modal Toggle
  if (elDrawerToggleBtn) {
    elDrawerToggleBtn.addEventListener('click', () => {
      if (elDrawer) elDrawer.classList.toggle('open');
      fetchTrades();
    });
  }

  if (elDrawerCloseBtn) {
    elDrawerCloseBtn.addEventListener('click', () => {
      if (elDrawer) elDrawer.classList.remove('open');
    });
  }

  // Kill Switch
  if (elKillSwitchBtn) {
    elKillSwitchBtn.addEventListener('click', async () => {
      try {
        const resp = await fetch('/api/kill-switch', { method: 'POST' });
        const res = await resp.json();
        if (res.is_kill_switch_active) {
          elKillSwitchBtn.classList.add('active');
          elKillSwitchBtn.textContent = 'KILL (ACTIVE)';
        } else {
          elKillSwitchBtn.classList.remove('active');
          elKillSwitchBtn.textContent = 'KILL SWITCH';
        }
      } catch (e) {
        console.error('Error toggling kill switch:', e);
      }
    });
  }

  // Mode Switch
  if (elPaperBtn) {
    elPaperBtn.addEventListener('click', async () => {
      try {
        const resp = await fetch('/api/mode', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mode: 'PAPER' })
        });
        if (resp.ok) {
          elPaperBtn.className = 'mode-pill active paper';
          elLiveBtn.className = 'mode-pill';
        }
      } catch (e) {
        console.error('Error switching to paper mode:', e);
      }
    });
  }

  if (elLiveBtn) {
    elLiveBtn.addEventListener('click', () => {
      if (elModal) {
        elModalInput.value = '';
        elModal.classList.add('open');
      }
    });
  }

  if (elModalCancel) {
    elModalCancel.addEventListener('click', () => {
      if (elModal) elModal.classList.remove('open');
    });
  }

  if (elModalSubmit) {
    elModalSubmit.addEventListener('click', async () => {
      const val = elModalInput.value.trim();
      if (val !== 'LIVE') {
        alert("You must type 'LIVE' to confirm.");
        return;
      }
      try {
        const resp = await fetch('/api/mode', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mode: 'LIVE', confirmation: 'LIVE' })
        });
        const res = await resp.json();
        if (resp.ok) {
          elLiveBtn.className = 'mode-pill active live';
          elPaperBtn.className = 'mode-pill';
          if (elModal) elModal.classList.remove('open');
        } else {
          alert(res.detail || 'Could not switch to LIVE mode.');
        }
      } catch (e) {
        alert('Error switching to LIVE mode.');
      }
    });
  }

  // App Initialization
  async function init() {
    initChart();

    // Setup collapsible sidebar
    if (elSidebarToggleBtn) {
      elSidebarToggleBtn.addEventListener('click', () => toggleSidebar());
    }
    if (elSidebarCloseBtn) {
      elSidebarCloseBtn.addEventListener('click', () => toggleSidebar(false));
    }

    await fetchStatus();

    // Default to ATM CE contract
    if (contracts.atm_ce) {
      await loadCandles(contracts.atm_ce.token);
    } else {
      await loadCandles('44596');
    }

    await fetchTrades();
    connectWebSocket();
  }

  document.addEventListener('DOMContentLoaded', init);
})();
