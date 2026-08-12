(function() {
  'use strict';

  var translationPromises = new Map();

  function initAllScatterPlots() {
    var containers = document.querySelectorAll('.hs-meta-scatter-wrapper');
    containers.forEach(function(wrapper, index) {
      initScatterPlot(wrapper, index);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAllScatterPlots);
  } else {
    initAllScatterPlots();
  }

  function initScatterPlot(wrapper, wrapperIndex) {
    var canvas = wrapper.querySelector('.hs-meta-scatter-canvas');
    var hoverInfo = wrapper.querySelector('.hs-meta-scatter-tooltip');
    var rankSelector = wrapper.querySelector('.hs-meta-scatter-rank-selector');
    if (!canvas || !hoverInfo) return;

    var apiUrl = normalizeApiUrl(wrapper.getAttribute('data-api-url') || 'https://api.kolodahearthstone.com');
    var translationsUrl = wrapper.getAttribute('data-archetypes-url') || '';
    var format = wrapper.getAttribute('data-format') || 'standard';
    var currentRank = wrapper.getAttribute('data-start-rank') || 'diamond_4to1';
    var showSelector = wrapper.getAttribute('data-show-selector') === 'yes';
    var baseWidth = parseInt(canvas.getAttribute('width'), 10) || 850;
    var baseHeight = parseInt(canvas.getAttribute('height'), 10) || 500;
    var currentStrategies = null;
    var currentTranslations = {};

    var RANK_LABELS = {
      'legend': 'Легенда',
      'diamond_4to1': 'Алмаз 4-1',
      'top_5k': 'Топ-5к легенды',
      'top_legend': 'Топ-1к легенды'
    };

    var AVAILABLE_RANKS = ['top_legend', 'top_5k', 'legend', 'diamond_4to1'];

    setupFullscreen(wrapper, function() {
      if (currentStrategies) {
        renderScatterPlot(canvas, hoverInfo, currentStrategies, currentTranslations, baseWidth, baseHeight, wrapperIndex);
      }
    });

    if (showSelector && rankSelector) {
      rankSelector.innerHTML = '';
      AVAILABLE_RANKS.forEach(function(rank) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'hs-vs-tab-btn rank-tab-btn ' + (rank === currentRank ? 'active' : '');
        btn.textContent = RANK_LABELS[rank] || rank;

        btn.addEventListener('click', function() {
          if (rank === currentRank && currentStrategies) return;
          rankSelector.querySelectorAll('.rank-tab-btn').forEach(function(b) { b.classList.remove('active'); });
          btn.classList.add('active');
          currentRank = rank;
          loadScatterData(currentRank);
        });

        rankSelector.appendChild(btn);
      });
    }

    function loadScatterData(rank) {
      var sourceId = 'hsguru_meta_' + format + '_' + rank;
      hoverInfo.textContent = 'Загрузка данных графика...';
      clearCanvas(canvas, baseWidth, baseHeight);

      Promise.all([
        fetch(apiUrl + '/datasets/' + encodeURIComponent(sourceId), { credentials: 'omit' }).then(function(res) {
          if (!res.ok) throw new Error('HTTP ' + res.status);
          return res.json();
        }),
        loadTranslationMap(translationsUrl).catch(function() { return {}; })
      ])
        .then(function(result) {
          var payload = result[0];
          currentTranslations = result[1] || {};
          var strategies = extractStrategies(payload);
          if (!strategies) {
            throw new Error('Некорректная структура данных');
          }
          currentStrategies = strategies;
          renderScatterPlot(canvas, hoverInfo, currentStrategies, currentTranslations, baseWidth, baseHeight, wrapperIndex);
        })
        .catch(function(err) {
          hoverInfo.innerHTML = '<span style="color: #ff3b30;">Ошибка загрузки графика (' + escapeHtml(sourceId) + '): ' + escapeHtml(err.message) + '</span>';
        });
    }

    loadScatterData(currentRank);
  }

  function normalizeApiUrl(url) {
    return String(url || '').replace(/\/+$/, '');
  }

  function extractStrategies(payload) {
    if (!payload || typeof payload !== 'object') {
      return null;
    }

    if (payload.data && payload.data.structured && Array.isArray(payload.data.structured.strategies)) {
      return payload.data.structured.strategies;
    }
    if (payload.structured && Array.isArray(payload.structured.strategies)) {
      return payload.structured.strategies;
    }
    if (payload.data && Array.isArray(payload.data.strategies)) {
      return payload.data.strategies;
    }
    if (Array.isArray(payload.strategies)) {
      return payload.strategies;
    }

    return null;
  }

  function renderScatterPlot(canvas, hoverInfo, strategies, translations, baseWidth, baseHeight, wrapperIndex) {
    var CLASS_COLORS = {
      'Death Knight': '#008f7d',
      'Demon Hunter': '#a330c9',
      'Druid': '#ff7d0a',
      'Hunter': '#abd473',
      'Mage': '#40c7eb',
      'Paladin': '#f58cba',
      'Priest': '#ffffff',
      'Rogue': '#fff569',
      'Shaman': '#0070de',
      'Warlock': '#8787ed',
      'Warrior': '#c79c6e',
      'Neutral': '#999999'
    };

    var CLASS_LABELS = {
      'Death Knight': 'Рыцарь смерти',
      'Demon Hunter': 'Охотник на демонов',
      'Druid': 'Друид',
      'Hunter': 'Охотник',
      'Mage': 'Маг',
      'Paladin': 'Паладин',
      'Priest': 'Жрец',
      'Rogue': 'Разбойник',
      'Shaman': 'Шаман',
      'Warlock': 'Чернокнижник',
      'Warrior': 'Воин',
      'Neutral': 'Нейтральный'
    };

    var parsedPoints = strategies.map(function(s) {
      var name = getFirstValue(s, ['Archetype', 'archetype', 'strategy', 'name']) || '';
      var winrateStr = getFirstValue(s, ['Winrate↓', 'Winrate', 'winrate', 'win_rate', 'wr']) || '0';
      var popularityStr = getFirstValue(s, ['Popularity', 'popularity', 'popularity_percent', 'pop']) || '0%';
      var winrate = parseNumber(winrateStr);
      var popularity = parseNumber(popularityStr);
      var hsClass = detectClass(name);

      return {
        name: name,
        displayName: translateName(name, translations),
        winrate: winrate,
        popularity: popularity,
        popularityStr: popularityStr,
        turns: getFirstValue(s, ['Turns', 'turns']) || '',
        duration: getFirstValue(s, ['Duration', 'duration']) || '',
        climbingSpeed: getFirstValue(s, ['Climbing Speed', 'climbing_speed', 'climbingSpeed']) || '',
        hsClass: hsClass
      };
    }).filter(function(p) {
      return p.winrate > 0 && p.name;
    });

    var prepared = prepareCanvas(canvas, baseWidth, baseHeight);
    var ctx = prepared.ctx;
    var width = prepared.width;
    var height = prepared.height;

    if (parsedPoints.length === 0) {
      ctx.fillStyle = '#1e1e24';
      ctx.fillRect(0, 0, width, height);
      ctx.fillStyle = '#d0d0e0';
      ctx.font = '14px sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('Нет данных для отображения графика.', width / 2, height / 2);
      hoverInfo.textContent = 'API ответил, но в наборе нет стратегий с винрейтом.';
      return;
    }

    var paddingLeft = width < 560 ? 48 : 64;
    var paddingRight = width < 560 ? 18 : 34;
    var paddingTop = 28;
    var paddingBottom = width < 560 ? 44 : 52;
    var chartWidth = width - paddingLeft - paddingRight;
    var chartHeight = height - paddingTop - paddingBottom;
    var xMin = 35;
    var xMax = 65;
    var yMin = 0;
    var maxPop = Math.max.apply(Math, parsedPoints.map(function(p) { return p.popularity; }).concat([5]));
    var yMax = Math.max(5, Math.ceil(maxPop / 5) * 5);
    var hoveredPoint = null;
    var topLabels = {};

    parsedPoints
      .slice()
      .sort(function(a, b) { return b.popularity - a.popularity; })
      .slice(0, width < 560 ? 6 : 12)
      .forEach(function(p) {
        topLabels[p.name] = true;
      });

    function mapX(wr) {
      return paddingLeft + ((wr - xMin) / (xMax - xMin)) * chartWidth;
    }

    function mapY(pop) {
      return height - paddingBottom - ((pop - yMin) / (yMax - yMin)) * chartHeight;
    }

    function draw() {
      ctx.fillStyle = '#1e1e24';
      ctx.fillRect(0, 0, width, height);

      ctx.strokeStyle = '#30303d';
      ctx.lineWidth = 1;
      ctx.fillStyle = '#a7a7b6';
      ctx.font = '12px sans-serif';
      ctx.textAlign = 'right';
      ctx.textBaseline = 'middle';

      var yStep = yMax <= 10 ? 2 : 5;
      for (var pop = yMin; pop <= yMax; pop += yStep) {
        var y = mapY(pop);
        ctx.beginPath();
        ctx.moveTo(paddingLeft, y);
        ctx.lineTo(width - paddingRight, y);
        ctx.stroke();
        ctx.fillText(pop + '%', paddingLeft - 9, y);
      }

      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      for (var wr = xMin; wr <= xMax; wr += 5) {
        var x = mapX(wr);
        ctx.beginPath();
        ctx.moveTo(x, paddingTop);
        ctx.lineTo(x, height - paddingBottom);
        ctx.stroke();
        ctx.fillText(wr + '%', x, height - paddingBottom + 8);
      }

      ctx.strokeStyle = '#5a5a68';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(paddingLeft, paddingTop);
      ctx.lineTo(paddingLeft, height - paddingBottom);
      ctx.lineTo(width - paddingRight, height - paddingBottom);
      ctx.stroke();

      ctx.fillStyle = '#d8d8e2';
      ctx.font = 'bold 13px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('Винрейт', paddingLeft + chartWidth / 2, height - paddingBottom + 30);

      ctx.save();
      ctx.translate(16, paddingTop + chartHeight / 2);
      ctx.rotate(-Math.PI / 2);
      ctx.fillText('Популярность', 0, 0);
      ctx.restore();

      parsedPoints.forEach(function(p) {
        if (p !== hoveredPoint) drawPoint(p, false, topLabels[p.name]);
      });

      if (hoveredPoint) {
        drawPoint(hoveredPoint, true, true);
      }
    }

    function drawPoint(p, isHovered, withLabel) {
      var cx = mapX(p.winrate);
      var cy = mapY(p.popularity);
      var color = CLASS_COLORS[p.hsClass] || CLASS_COLORS.Neutral;
      var radius = isHovered ? 8 : Math.max(4.5, Math.min(8, 4.5 + (p.popularity / yMax) * 5));

      if (isHovered) {
        ctx.beginPath();
        ctx.arc(cx, cy, radius + 6, 0, 2 * Math.PI);
        ctx.fillStyle = 'rgba(255, 255, 255, 0.18)';
        ctx.fill();
      }

      ctx.beginPath();
      ctx.arc(cx, cy, radius, 0, 2 * Math.PI);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.strokeStyle = isHovered ? '#fff' : 'rgba(0, 0, 0, 0.62)';
      ctx.lineWidth = isHovered ? 2.2 : 1.2;
      ctx.stroke();

      if (!withLabel) return;

      var label = p.displayName || p.name;
      ctx.font = isHovered ? 'bold 12px sans-serif' : 'bold 10.5px sans-serif';
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      ctx.lineJoin = 'round';
      ctx.miterLimit = 2;
      ctx.strokeStyle = 'rgba(0, 0, 0, 0.86)';
      ctx.lineWidth = 4;
      ctx.strokeText(label, cx + radius + 5, cy);
      ctx.fillStyle = isHovered ? '#fff' : '#e5e5ed';
      ctx.fillText(label, cx + radius + 5, cy);
    }

    canvas.onmousemove = function(e) {
      var rect = canvas.getBoundingClientRect();
      var mouseX = e.clientX - rect.left;
      var mouseY = e.clientY - rect.top;
      var found = null;
      var minDistance = 16;

      for (var i = 0; i < parsedPoints.length; i++) {
        var p = parsedPoints[i];
        var cx = mapX(p.winrate);
        var cy = mapY(p.popularity);
        var dist = Math.hypot(mouseX - cx, mouseY - cy);
        if (dist < minDistance) {
          minDistance = dist;
          found = p;
        }
      }

      if (found !== hoveredPoint) {
        hoveredPoint = found;
        canvas.style.cursor = hoveredPoint ? 'pointer' : 'default';

        if (hoveredPoint) {
          hoverInfo.innerHTML =
            '<span style="color: ' + CLASS_COLORS[hoveredPoint.hsClass] + '; font-weight: 800;">' + escapeHtml(hoveredPoint.displayName) + '</span>' +
            ' <span style="color:#8f8fa0;">(' + escapeHtml(CLASS_LABELS[hoveredPoint.hsClass] || hoveredPoint.hsClass) + ')</span> · ' +
            'Винрейт: <strong style="color: #4cd137">' + hoveredPoint.winrate + '%</strong> · ' +
            'Популярность: <strong style="color: #00a8ff">' + escapeHtml(hoveredPoint.popularityStr) + '</strong>' +
            (hoveredPoint.turns ? ' · Ходов: <strong>' + escapeHtml(hoveredPoint.turns) + '</strong>' : '') +
            (hoveredPoint.climbingSpeed ? ' · Скорость: <strong>' + escapeHtml(hoveredPoint.climbingSpeed) + '</strong>' : '');
        } else {
          hoverInfo.textContent = 'Наведите на точку, чтобы увидеть детали';
        }
        draw();
      }
    };

    canvas.onmouseleave = function() {
      if (hoveredPoint) {
        hoveredPoint = null;
        hoverInfo.textContent = 'Наведите на точку, чтобы увидеть детали';
        canvas.style.cursor = 'default';
        draw();
      }
    };

    hoverInfo.textContent = 'Наведите на точку, чтобы увидеть детали';
    draw();
  }

  function clearCanvas(canvas, baseWidth, baseHeight) {
    var prepared = prepareCanvas(canvas, baseWidth, baseHeight);
    prepared.ctx.fillStyle = '#1e1e24';
    prepared.ctx.fillRect(0, 0, prepared.width, prepared.height);
  }

  function prepareCanvas(canvas, baseWidth, baseHeight) {
    var rect = canvas.getBoundingClientRect();
    var ratio = baseHeight / baseWidth;
    var width = Math.max(280, Math.round(rect.width || baseWidth));
    var height = Math.max(220, Math.round(rect.height || (width * ratio)));
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    var targetWidth = Math.round(width * dpr);
    var targetHeight = Math.round(height * dpr);

    if (canvas.width !== targetWidth || canvas.height !== targetHeight) {
      canvas.width = targetWidth;
      canvas.height = targetHeight;
    }

    var ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    return { ctx: ctx, width: width, height: height };
  }

  function detectClass(name) {
    var nameLower = String(name || '').toLowerCase();
    if (nameLower.indexOf('dh') !== -1 || nameLower.indexOf('demon hunter') !== -1 || nameLower.indexOf('demonhunter') !== -1) return 'Demon Hunter';
    if (nameLower.indexOf('dk') !== -1 || nameLower.indexOf('death knight') !== -1 || nameLower.indexOf('deathknight') !== -1) return 'Death Knight';
    if (nameLower.indexOf('druid') !== -1) return 'Druid';
    if (nameLower.indexOf('hunter') !== -1) return 'Hunter';
    if (nameLower.indexOf('mage') !== -1) return 'Mage';
    if (nameLower.indexOf('paladin') !== -1 || nameLower.indexOf('turnadin') !== -1) return 'Paladin';
    if (nameLower.indexOf('priest') !== -1) return 'Priest';
    if (nameLower.indexOf('rogue') !== -1) return 'Rogue';
    if (nameLower.indexOf('shaman') !== -1) return 'Shaman';
    if (nameLower.indexOf('warlock') !== -1 || nameLower.indexOf('egglock') !== -1 || nameLower.indexOf('rafaamlock') !== -1) return 'Warlock';
    if (nameLower.indexOf('warrior') !== -1) return 'Warrior';
    return 'Neutral';
  }

  function loadTranslationMap(url) {
    if (!url) return Promise.resolve({});
    if (translationPromises.has(url)) {
      return translationPromises.get(url);
    }

    var promise = readCachedMap('hs_parses_archetypes_' + hashString(url), 6 * 60 * 60 * 1000);
    if (!promise) {
      promise = fetch(url, { credentials: 'same-origin' })
        .then(function(res) {
          if (!res.ok) throw new Error('HTTP ' + res.status);
          return res.json();
        })
        .then(function(payload) {
          var map = normalizeTranslationPayload(payload);
          writeCachedMap('hs_parses_archetypes_' + hashString(url), map);
          return map;
        });
    }

    translationPromises.set(url, promise);
    return promise;
  }

  function normalizeTranslationPayload(payload) {
    var map = {};
    if (!payload) return map;

    var items = payload.items || payload;
    if (!items) return map;

    if (!Array.isArray(items) && typeof items === 'object') {
      Object.keys(items).forEach(function(en) {
        if (en && items[en]) {
          map[normalizeKey(en)] = String(items[en]).trim();
        }
      });
      return map;
    }

    if (Array.isArray(items)) {
      items.forEach(function(item) {
        if (!item || typeof item !== 'object') return;
        var en = item.name_en || item.eng || item.source || '';
        var ru = item.name_ru || item.rus || item.translated || '';
        if (en && ru) {
          map[normalizeKey(en)] = String(ru).trim();
        }
      });
    }

    return map;
  }

  function translateName(name, translations) {
    if (!name || !translations) return name;
    var key = normalizeKey(name);
    if (translations[key]) return translations[key];

    var best = '';
    var bestLen = 0;
    Object.keys(translations).forEach(function(candidate) {
      if (candidate && key.indexOf(candidate) !== -1 && candidate.length > bestLen) {
        best = translations[candidate];
        bestLen = candidate.length;
      }
    });

    return best || name;
  }

  function normalizeKey(value) {
    return String(value || '').toLowerCase().replace(/\s+/g, ' ').trim();
  }

  function readCachedMap(key, ttl) {
    try {
      var raw = window.localStorage && window.localStorage.getItem(key);
      if (!raw) return null;
      var cached = JSON.parse(raw);
      if (!cached || !cached.time || !cached.map || Date.now() - cached.time > ttl) return null;
      return Promise.resolve(cached.map);
    } catch (e) {
      return null;
    }
  }

  function writeCachedMap(key, map) {
    try {
      if (window.localStorage) {
        window.localStorage.setItem(key, JSON.stringify({ time: Date.now(), map: map }));
      }
    } catch (e) {
      // Storage may be full or disabled; graph still works without this cache.
    }
  }

  function setupFullscreen(wrapper, redraw) {
    var button = wrapper.querySelector('.hs-graph-fullscreen-btn');
    if (!button) return;

    var openLabel = button.textContent.trim() || 'На весь экран';
    var exitLabel = button.getAttribute('data-exit-label') || 'Закрыть полноэкранный режим';

    function isFullscreen() {
      return document.fullscreenElement === wrapper || wrapper.classList.contains('hs-graph-fullscreen-active');
    }

    function refresh() {
      button.textContent = isFullscreen() ? exitLabel : openLabel;
      window.setTimeout(redraw, 80);
    }

    button.addEventListener('click', function() {
      if (isFullscreen()) {
        if (document.fullscreenElement && document.exitFullscreen) {
          document.exitFullscreen();
        } else {
          wrapper.classList.remove('hs-graph-fullscreen-active');
          refresh();
        }
        return;
      }

      if (wrapper.requestFullscreen) {
        wrapper.requestFullscreen().catch(function() {
          wrapper.classList.add('hs-graph-fullscreen-active');
          refresh();
        });
      } else {
        wrapper.classList.add('hs-graph-fullscreen-active');
        refresh();
      }
    });

    document.addEventListener('fullscreenchange', refresh);
    document.addEventListener('keydown', function(e) {
      if (e.key === 'Escape' && wrapper.classList.contains('hs-graph-fullscreen-active')) {
        wrapper.classList.remove('hs-graph-fullscreen-active');
        refresh();
      }
    });
    window.addEventListener('resize', debounce(redraw, 120));
  }

  function debounce(fn, delay) {
    var timeout = null;
    return function() {
      window.clearTimeout(timeout);
      timeout = window.setTimeout(fn, delay);
    };
  }

  function getFirstValue(source, keys) {
    for (var i = 0; i < keys.length; i++) {
      if (source[keys[i]] !== undefined && source[keys[i]] !== null && source[keys[i]] !== '') {
        return source[keys[i]];
      }
    }
    return null;
  }

  function parseNumber(value) {
    var match = String(value || '').match(/-?[\d.,]+/);
    if (!match) {
      return 0;
    }

    return parseFloat(match[0].replace(',', '.')) || 0;
  }

  function hashString(value) {
    var hash = 0;
    var str = String(value || '');
    for (var i = 0; i < str.length; i++) {
      hash = ((hash << 5) - hash) + str.charCodeAt(i);
      hash |= 0;
    }
    return String(Math.abs(hash));
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }
})();
