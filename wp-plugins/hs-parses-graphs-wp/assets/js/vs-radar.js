(function() {
  'use strict';

  var activeAnimations = new Map();
  var mapPromises = new Map();

  function initAllRadarCharts() {
    var containers = document.querySelectorAll('.hs-vs-radar-wrapper');
    containers.forEach(function(wrapper, index) {
      initRadarChart(wrapper, index);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAllRadarCharts);
  } else {
    initAllRadarCharts();
  }

  function initRadarChart(wrapper, wrapperIndex) {
    var canvas = wrapper.querySelector('.hs-vs-radar-canvas');
    var hoverInfo = wrapper.querySelector('.hs-vs-radar-hover-info');
    var classTabs = wrapper.querySelector('.hs-vs-class-tabs');
    var archTabs = wrapper.querySelector('.hs-vs-archetype-tabs');
    var searchInput = wrapper.querySelector('.hs-vs-radar-search');
    var resetBtn = wrapper.querySelector('.hs-vs-radar-reset-btn');
    var nodesTableBody = wrapper.querySelector('.hs-vs-nodes-table tbody');
    var cardTitle = wrapper.querySelector('.hs-vs-selected-card-title');
    var cardDetails = wrapper.querySelector('.hs-vs-selected-card-details');

    if (!canvas) return;

    var apiUrl = normalizeApiUrl(wrapper.getAttribute('data-api-url') || 'https://api.kolodahearthstone.com');
    var archetypesUrl = wrapper.getAttribute('data-archetypes-url') || '';
    var cardTranslationsUrl = wrapper.getAttribute('data-card-translations-url') || '';
    var startClass = wrapper.getAttribute('data-start-class') || '';
    var lockClass = wrapper.getAttribute('data-lock-class') === 'yes';

    if (hoverInfo) {
      hoverInfo.textContent = 'Загрузка радаров Vicious Syndicate...';
    }

    Promise.all([
      fetch(apiUrl + '/datasets/vicious_syndicate_radars', { credentials: 'omit' }).then(function(res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      }),
      loadRemoteMap(archetypesUrl, 'hs_parses_archetypes_', 6 * 60 * 60 * 1000, normalizeTranslationPayload).catch(function() { return {}; })
    ])
      .then(function(result) {
        var data = normalizeRadarPayload(result[0]);
        if (!data || !Array.isArray(data.radars) || data.radars.length === 0) {
          throw new Error('Некорректная структура данных');
        }
        if (hoverInfo) {
          hoverInfo.textContent = 'Наведите на карту для деталей';
        }
        setupRadarGraph(
          wrapper,
          data,
          canvas,
          hoverInfo,
          classTabs,
          archTabs,
          searchInput,
          resetBtn,
          nodesTableBody,
          cardTitle,
          cardDetails,
          startClass,
          lockClass,
          wrapperIndex,
          result[1] || {},
          cardTranslationsUrl
        );
      })
      .catch(function(err) {
        if (hoverInfo) {
          hoverInfo.innerHTML = '<span style="color: #ff3b30;">Ошибка загрузки радаров: ' + escapeHtml(err.message) + '</span>';
        }
      });
  }

  function normalizeApiUrl(url) {
    return String(url || '').replace(/\/+$/, '');
  }

  function normalizeRadarPayload(payload) {
    if (!payload || typeof payload !== 'object') {
      return null;
    }

    var data = payload.data || payload;
    if (data.structured && typeof data.structured === 'object') {
      data = data.structured;
    }

    if (!Array.isArray(data.radars)) {
      return null;
    }

    var radars = data.radars.filter(function(radar) {
      return radar && radar.class && Array.isArray(radar.nodes) && Array.isArray(radar.edges);
    });

    return {
      classes_summary: Array.isArray(data.classes_summary) ? data.classes_summary : buildClassesSummary(radars),
      radars: radars,
      total_radars: data.total_radars || radars.length,
      type: data.type || 'vicious_syndicate_radars'
    };
  }

  function buildClassesSummary(radars) {
    var seen = {};
    var summary = [];

    radars.forEach(function(radar) {
      if (!seen[radar.class]) {
        seen[radar.class] = {
          class: radar.class,
          radars: 0,
          archetypes: 0,
          has_archetypes: false
        };
        summary.push(seen[radar.class]);
      }

      seen[radar.class].radars += 1;
      if (radar.archetype) {
        seen[radar.class].archetypes += 1;
        seen[radar.class].has_archetypes = true;
      }
    });

    return summary;
  }

  function setupRadarGraph(wrapper, data, canvas, hoverInfo, classTabs, archTabs, searchInput, resetBtn, nodesTableBody, cardTitle, cardDetails, startClass, lockClass, wrapperIndex, archetypeTranslations, cardTranslationsUrl) {
    var currentClass = null;
    var currentArchetype = null;
    var nodes = [];
    var edges = [];
    var nodeByName = {};
    var topLabelNames = {};
    var draggedNode = null;
    var hoveredNode = null;
    var selectedNode = null;
    var searchText = '';
    var cardTranslations = {};
    var cardInfo = {};
    var baseWidth = parseInt(canvas.getAttribute('width'), 10) || 750;
    var baseHeight = parseInt(canvas.getAttribute('height'), 10) || 750;
    var canvasWidth = baseWidth;
    var canvasHeight = baseHeight;
    var centerX = canvasWidth / 2;
    var centerY = canvasHeight / 2;
    var ctx = null;

    var CLASS_TRANSLATIONS = {
      'DeathKnight': 'Рыцарь смерти',
      'DemonHunter': 'Охотник на демонов',
      'Druid': 'Друид',
      'Hunter': 'Охотник',
      'Mage': 'Маг',
      'Paladin': 'Паладин',
      'Priest': 'Жрец',
      'Rogue': 'Разбойник',
      'Shaman': 'Шаман',
      'Warlock': 'Чернокнижник',
      'Warrior': 'Воин'
    };

    var CLASS_COLORS_BORDER = {
      'DeathKnight': '#008f7d',
      'DemonHunter': '#a330c9',
      'Druid': '#ff7d0a',
      'Hunter': '#abd473',
      'Mage': '#40c7eb',
      'Paladin': '#f58cba',
      'Priest': '#ffffff',
      'Rogue': '#fff569',
      'Shaman': '#0070de',
      'Warlock': '#8787ed',
      'Warrior': '#c79c6e'
    };

    resizeCanvas(false);
    setupFullscreen(wrapper, function() {
      resizeCanvas(true);
    });

    if (startClass && data.classes_summary) {
      var foundClass = data.classes_summary.find(function(c) {
        return String(c.class).toLowerCase() === String(startClass).toLowerCase();
      });
      if (foundClass) {
        currentClass = foundClass.class;
      }
    }

    if (!currentClass && data.classes_summary && data.classes_summary[0]) {
      currentClass = data.classes_summary[0].class;
    }

    if (!currentClass) {
      currentClass = data.radars[0] ? data.radars[0].class : null;
    }

    if (classTabs && data.classes_summary) {
      classTabs.innerHTML = '';

      if (lockClass) {
        classTabs.style.display = 'none';
      } else {
        classTabs.style.display = 'flex';
        data.classes_summary.forEach(function(c) {
          var btn = document.createElement('button');
          btn.type = 'button';
          btn.className = 'hs-vs-tab-btn class-tab-btn ' + (c.class === currentClass ? 'active' : '');
          btn.style.borderColor = CLASS_COLORS_BORDER[c.class] || '#444';
          btn.textContent = CLASS_TRANSLATIONS[c.class] || c.class;
          btn.setAttribute('data-class', c.class);

          btn.addEventListener('click', function() {
            classTabs.querySelectorAll('.class-tab-btn').forEach(function(b) { b.classList.remove('active'); });
            btn.classList.add('active');
            currentClass = c.class;
            currentArchetype = null;
            rebuildArchetypeTabs(currentClass);
            loadClassRadar(currentClass, null);
          });

          classTabs.appendChild(btn);
        });
      }
    }

    function rebuildArchetypeTabs(clsName) {
      if (!archTabs) return;
      archTabs.innerHTML = '';

      var classRadars = data.radars.filter(function(r) { return r.class === clsName; });
      classRadars.sort(function(a, b) {
        if (a.archetype === null) return -1;
        if (b.archetype === null) return 1;
        return String(a.archetype).localeCompare(String(b.archetype));
      });

      if (classRadars.length > 1) {
        archTabs.style.display = 'flex';
        classRadars.forEach(function(r, i) {
          var btn = document.createElement('button');
          btn.type = 'button';
          btn.className = 'hs-vs-tab-btn arch-tab-btn ' + (i === 0 ? 'active' : '');
          btn.textContent = r.archetype ? translateName(r.archetype, archetypeTranslations) : 'Общий класс';

          btn.addEventListener('click', function() {
            archTabs.querySelectorAll('.arch-tab-btn').forEach(function(b) { b.classList.remove('active'); });
            btn.classList.add('active');
            currentArchetype = r.archetype || null;
            loadClassRadar(currentClass, currentArchetype);
          });

          archTabs.appendChild(btn);
        });
      } else {
        archTabs.style.display = 'none';
      }
    }

    if (searchInput) {
      searchInput.addEventListener('input', function(e) {
        searchText = normalizeKey(e.target.value);
      });
    }

    if (resetBtn) {
      resetBtn.addEventListener('click', function() {
        if (searchInput) searchInput.value = '';
        searchText = '';
        selectedNode = null;
        hoveredNode = null;
        updateSelectedCardView();
        loadClassRadar(currentClass, currentArchetype);
      });
    }

    function resizeCanvas(scaleNodes) {
      var oldWidth = canvasWidth || baseWidth;
      var oldHeight = canvasHeight || baseHeight;
      var prepared = prepareCanvas(canvas, baseWidth, baseHeight);
      ctx = prepared.ctx;
      canvasWidth = prepared.width;
      canvasHeight = prepared.height;
      centerX = canvasWidth / 2;
      centerY = canvasHeight / 2;

      if (scaleNodes && nodes.length && oldWidth && oldHeight) {
        var scaleX = canvasWidth / oldWidth;
        var scaleY = canvasHeight / oldHeight;
        nodes.forEach(function(node) {
          node.x *= scaleX;
          node.y *= scaleY;
        });
      }
    }

    function loadClassRadar(clsName, archetype) {
      archetype = archetype || null;
      var radar = data.radars.find(function(r) { return r.class === clsName && (r.archetype || null) === archetype; });
      if (!radar) {
        if (hoverInfo) {
          hoverInfo.textContent = 'Нет данных для выбранного класса или архетипа.';
        }
        return;
      }

      selectedNode = null;
      hoveredNode = null;
      updateSelectedCardView();

      var deckSec = wrapper.querySelector('.hs-vs-deck-code-section');
      var deckInp = wrapper.querySelector('.hs-vs-deck-code-input');
      var deckBtn = wrapper.querySelector('.hs-vs-deck-code-copy-btn');

      if (deckSec && deckInp && deckBtn) {
        if (radar.deck_code) {
          deckInp.value = radar.deck_code;
          deckSec.style.display = 'block';
          deckBtn.onclick = function() {
            navigator.clipboard.writeText(radar.deck_code);
            deckBtn.textContent = 'Скопировано';
            deckBtn.style.background = '#ff9f1c';
            deckBtn.style.color = '#000';
            setTimeout(function() {
              deckBtn.textContent = 'Копировать';
              deckBtn.style.background = '#2ec4b6';
              deckBtn.style.color = '#fff';
            }, 1500);
          };
        } else {
          deckSec.style.display = 'none';
        }
      }

      var radarEdges = Array.isArray(radar.edges) ? radar.edges : [];
      nodes = (Array.isArray(radar.nodes) ? radar.nodes : []).map(function(n, index) {
        var seed = hashString(n.name || String(index));
        var angle = (seed % 6283) / 1000;
        var spread = Math.max(120, Math.min(canvasWidth, canvasHeight) * 0.29);
        var radiusOffset = ((seed >> 3) % 100) / 100;
        var placementRadius = spread * (0.62 + radiusOffset * 0.56);
        var linksCount = radarEdges.filter(function(e) { return e.source === n.name || e.target === n.name; }).length;
        var displayName = getDisplayName(n.name);

        return Object.assign({}, n, {
          name: n.name,
          displayName: displayName,
          cardInfo: getCardInfo(n.name),
          searchName: normalizeKey(n.name + ' ' + displayName),
          x: centerX + Math.cos(angle) * placementRadius,
          y: centerY + Math.sin(angle) * placementRadius,
          vx: 0,
          vy: 0,
          radius: Math.max(6, parseNumber(n.radius || 8)),
          linksCount: linksCount
        });
      });

      edges = radarEdges.map(function(e) {
        return Object.assign({}, e, {
          weight: parseNumber(e.weight || 0),
          length: parseNumber(e.length || 220) || 220
        });
      });

      nodeByName = {};
      nodes.forEach(function(n) {
        nodeByName[n.name] = n;
      });

      topLabelNames = {};
      nodes
        .slice()
        .sort(function(a, b) { return (b.linksCount + b.radius) - (a.linksCount + a.radius); })
        .slice(0, Math.min(10, nodes.length))
        .forEach(function(n) {
          topLabelNames[n.name] = true;
        });

      updateNodesTable();
      loadCardTranslationsForNodes(nodes);
    }

    function loadCardTranslationsForNodes(currentNodes) {
      if (!cardTranslationsUrl || !currentNodes.length) return;

      var names = currentNodes.map(function(node) { return node.name; });
      loadCardTranslationMap(cardTranslationsUrl, names).then(function(map) {
        var changed = false;
        Object.keys(map).forEach(function(key) {
          var entry = normalizeCardInfo(map[key]);
          if (!entry) return;
          if (!cardTranslations[key] || !cardInfo[key]) {
            cardTranslations[key] = entry.name || '';
            cardInfo[key] = entry;
            changed = true;
          }
        });

        if (!changed) return;

        currentNodes.forEach(function(node) {
          node.cardInfo = getCardInfo(node.name);
          node.displayName = getDisplayName(node.name);
          node.searchName = normalizeKey(node.name + ' ' + node.displayName);
        });
        updateNodesTable();
        updateSelectedCardView();
      }).catch(function() {
        // English names remain usable if the translation endpoint is unavailable.
      });
    }

    function getCardInfo(name) {
      return cardInfo[normalizeKey(name)] || null;
    }

    function getDisplayName(name) {
      var info = getCardInfo(name);
      if (info && info.name) return info.name;
      return translateName(name, cardTranslations);
    }

    function normalizeCardInfo(value) {
      if (!value) return null;
      if (typeof value === 'string') {
        return {
          name: value,
          image: '',
          imageRaw: '',
          rarity: 'common'
        };
      }

      if (typeof value !== 'object') return null;

      return {
        name: String(value.name || value.name_ru || value.rus || value.translated || '').trim(),
        image: String(value.image || '').trim(),
        imageRaw: String(value.image_raw || value.raw || '').trim(),
        rarity: sanitizeRarity(value.rarity)
      };
    }

    function renderCardTooltipHtml(nodeOrCard, label) {
      var info = nodeOrCard && nodeOrCard.cardInfo ? nodeOrCard.cardInfo : null;
      if (!info && nodeOrCard && nodeOrCard.name) {
        info = getCardInfo(nodeOrCard.name);
      }

      var safeLabel = escapeHtml(label || (info && info.name) || '');
      if (!info || !info.image) {
        return safeLabel;
      }

      return '<span class="hs-card-tooltip hs-rarity-' + escapeAttr(sanitizeRarity(info.rarity)) + '"' +
        ' data-image="' + escapeAttr(info.image) + '"' +
        ' data-image-raw="' + escapeAttr(info.imageRaw || '') + '">' +
        safeLabel +
        '</span>';
    }

    function sanitizeRarity(value) {
      value = String(value || '').toLowerCase();
      return /^(common|rare|epic|legendary)$/.test(value) ? value : 'common';
    }

    function updateNodesTable() {
      if (!nodesTableBody) return;
      nodesTableBody.innerHTML = '';

      var sortedNodes = nodes.slice().sort(function(a, b) {
        return b.radius - a.radius;
      });

      sortedNodes.forEach(function(n) {
        var tr = document.createElement('tr');
        tr.style.cursor = 'pointer';
        tr.innerHTML =
          '<td><strong>' + renderCardTooltipHtml(n, n.displayName) + '</strong></td>' +
          '<td>' + n.radius.toFixed(1) + '</td>' +
          '<td>' + n.linksCount + '</td>';

        tr.addEventListener('click', function() {
          var matchingNode = nodeByName[n.name];
          if (matchingNode) {
            selectedNode = matchingNode;
            updateSelectedCardView();
          }
        });

        nodesTableBody.appendChild(tr);
      });
    }

    function updateSelectedCardView() {
      if (!cardTitle || !cardDetails) return;

      if (!selectedNode) {
        cardTitle.textContent = 'Выберите карту на графе';
        cardDetails.innerHTML = '<p class="muted" style="color: #888;">Нажмите на узел, чтобы увидеть сильные связи.</p>';
        return;
      }

      cardTitle.innerHTML = renderCardTooltipHtml(selectedNode, selectedNode.displayName);

      var conn = edges
        .filter(function(e) { return e.source === selectedNode.name || e.target === selectedNode.name; })
        .map(function(e) {
          var otherName = e.source === selectedNode.name ? e.target : e.source;
          var otherNode = nodeByName[otherName];
          return {
            name: otherName,
            cardInfo: otherNode ? otherNode.cardInfo : getCardInfo(otherName),
            displayName: otherNode ? otherNode.displayName : getDisplayName(otherName),
            weight: e.weight
          };
        })
        .sort(function(a, b) { return b.weight - a.weight; })
        .slice(0, 10);

      var html = '<p style="margin: 4px 0;"><strong>Популярность:</strong> ' + selectedNode.radius.toFixed(1) + '</p>';
      html += '<h5 style="margin: 12px 0 6px; border-bottom: 1px solid #333; padding-bottom: 4px; font-size: 0.85rem; color: #ff9f1c;">Сильнейшие связи</h5>';
      if (conn.length === 0) {
        html += '<p class="muted" style="color: #777;">Связи не найдены.</p>';
      } else {
        html += '<ul>';
        conn.forEach(function(c) {
          html += '<li><strong>' + renderCardTooltipHtml(c, c.displayName) + '</strong> (' + (c.weight * 100).toFixed(0) + '%)</li>';
        });
        html += '</ul>';
      }

      cardDetails.innerHTML = html;
    }

    function getMousePos(evt) {
      var rect = canvas.getBoundingClientRect();
      return {
        x: evt.clientX - rect.left,
        y: evt.clientY - rect.top
      };
    }

    canvas.addEventListener('mousedown', function(e) {
      var pos = getMousePos(e);
      var clicked = getNodeAt(pos.x, pos.y);

      if (clicked) {
        draggedNode = clicked;
        selectedNode = clicked;
        updateSelectedCardView();
        canvas.style.cursor = 'grabbing';
      }
    });

    canvas.addEventListener('mousemove', function(e) {
      var pos = getMousePos(e);

      if (draggedNode) {
        draggedNode.x = pos.x;
        draggedNode.y = pos.y;
        draggedNode.vx = 0;
        draggedNode.vy = 0;
        return;
      }

      var found = getNodeAt(pos.x, pos.y);
      hoveredNode = found;

      if (found) {
        canvas.style.cursor = 'pointer';
        if (hoverInfo) {
          hoverInfo.innerHTML = 'Карта: <strong style="color: #ff9f1c">' + escapeHtml(found.displayName) + '</strong> · Популярность: ' + found.radius.toFixed(1) + ' · Связей: ' + found.linksCount;
        }
      } else {
        canvas.style.cursor = 'grab';
        if (hoverInfo) {
          hoverInfo.textContent = 'Наведите на карту для деталей';
        }
      }
    });

    window.addEventListener('mouseup', function() {
      if (draggedNode) {
        draggedNode = null;
        canvas.style.cursor = 'grab';
      }
    });

    canvas.addEventListener('touchstart', function(e) {
      if (e.touches.length > 0) {
        var touch = e.touches[0];
        canvas.dispatchEvent(new MouseEvent('mousedown', { clientX: touch.clientX, clientY: touch.clientY }));
      }
    }, { passive: true });

    canvas.addEventListener('touchmove', function(e) {
      if (e.touches.length > 0) {
        var touch = e.touches[0];
        canvas.dispatchEvent(new MouseEvent('mousemove', { clientX: touch.clientX, clientY: touch.clientY }));
      }
    }, { passive: true });

    canvas.addEventListener('touchend', function() {
      window.dispatchEvent(new MouseEvent('mouseup', {}));
    });

    function getNodeAt(x, y) {
      for (var i = nodes.length - 1; i >= 0; i--) {
        var n = nodes[i];
        var dx = n.x - x;
        var dy = n.y - y;
        var dist = Math.sqrt(dx * dx + dy * dy);
        if (dist <= n.radius + 6) {
          return n;
        }
      }
      return null;
    }

    rebuildArchetypeTabs(currentClass);
    loadClassRadar(currentClass, null);

    if (activeAnimations.has(wrapperIndex)) {
      cancelAnimationFrame(activeAnimations.get(wrapperIndex));
    }

    function tick() {
      if (!ctx) {
        resizeCanvas(false);
      }

      for (var i = 0; i < nodes.length; i++) {
        var n1 = nodes[i];
        var cdx = centerX - n1.x;
        var cdy = centerY - n1.y;
        var cdist = Math.sqrt(cdx * cdx + cdy * cdy) || 1;
        n1.vx += (cdx / cdist) * 0.12;
        n1.vy += (cdy / cdist) * 0.12;

        for (var j = i + 1; j < nodes.length; j++) {
          var n2 = nodes[j];
          var dx = n2.x - n1.x;
          var dy = n2.y - n1.y;
          var dist = Math.sqrt(dx * dx + dy * dy) || 1;
          var minDist = n1.radius + n2.radius + 48;
          if (dist < minDist) {
            var force = (minDist - dist) * 0.34;
            var fx = (dx / dist) * force;
            var fy = (dy / dist) * force;
            n1.vx -= fx;
            n1.vy -= fy;
            n2.vx += fx;
            n2.vy += fy;
          }
        }
      }

      for (var k = 0; k < edges.length; k++) {
        var e = edges[k];
        var sourceNode = nodeByName[e.source];
        var targetNode = nodeByName[e.target];
        if (sourceNode && targetNode && e.weight > 0.1) {
          var ex = targetNode.x - sourceNode.x;
          var ey = targetNode.y - sourceNode.y;
          var edist = Math.sqrt(ex * ex + ey * ey) || 1;
          var desiredLen = Math.max(150, Math.min(300, e.length || 220));
          var springK = 0.012 * e.weight;
          var edgeForce = (edist - desiredLen) * springK;
          var efx = (ex / edist) * edgeForce;
          var efy = (ey / edist) * edgeForce;
          sourceNode.vx += efx;
          sourceNode.vy += efy;
          targetNode.vx -= efx;
          targetNode.vy -= efy;
        }
      }

      for (var m = 0; m < nodes.length; m++) {
        var n = nodes[m];
        if (n === draggedNode) continue;

        n.vx *= 0.84;
        n.vy *= 0.84;
        n.x += n.vx;
        n.y += n.vy;

        var margin = n.radius + 12;
        if (n.x < margin) { n.x = margin; n.vx = -n.vx * 0.45; }
        if (n.x > canvasWidth - margin) { n.x = canvasWidth - margin; n.vx = -n.vx * 0.45; }
        if (n.y < margin) { n.y = margin; n.vy = -n.vy * 0.45; }
        if (n.y > canvasHeight - margin) { n.y = canvasHeight - margin; n.vy = -n.vy * 0.45; }
      }

      drawFrame();

      var nextAnim = requestAnimationFrame(tick);
      activeAnimations.set(wrapperIndex, nextAnim);
    }

    function drawFrame() {
      ctx.clearRect(0, 0, canvasWidth, canvasHeight);
      ctx.fillStyle = '#17171d';
      ctx.fillRect(0, 0, canvasWidth, canvasHeight);

      for (var eIndex = 0; eIndex < edges.length; eIndex++) {
        drawEdge(edges[eIndex]);
      }

      for (var nIndex = 0; nIndex < nodes.length; nIndex++) {
        drawNode(nodes[nIndex]);
      }
    }

    function drawEdge(edgeObj) {
      var sourceNode = nodeByName[edgeObj.source];
      var targetNode = nodeByName[edgeObj.target];
      if (!sourceNode || !targetNode) return;

      var opacity = Math.min(0.38, edgeObj.weight * 0.5);
      var isEdgeHighlighted = false;

      if (searchText) {
        var sMatch = matchesSearch(sourceNode);
        var tMatch = matchesSearch(targetNode);
        if (sMatch || tMatch) {
          isEdgeHighlighted = true;
          opacity = Math.min(0.85, edgeObj.weight * 1.4);
        } else {
          opacity = 0.025;
        }
      } else if (selectedNode) {
        if (sourceNode.name === selectedNode.name || targetNode.name === selectedNode.name) {
          isEdgeHighlighted = true;
          opacity = Math.min(0.9, edgeObj.weight * 1.75);
        } else {
          opacity = 0.025;
        }
      } else if (edgeObj.weight < 0.16) {
        return;
      }

      ctx.beginPath();
      ctx.moveTo(sourceNode.x, sourceNode.y);
      ctx.lineTo(targetNode.x, targetNode.y);
      ctx.lineWidth = isEdgeHighlighted ? 2.4 : 1.0;
      ctx.strokeStyle = isEdgeHighlighted ? 'rgba(255, 159, 28, ' + opacity + ')' : 'rgba(220, 220, 230, ' + opacity + ')';
      ctx.stroke();
    }

    function drawNode(nodeObj) {
      var isNodeHighlighted = true;
      var drawBorder = false;

      if (searchText) {
        isNodeHighlighted = matchesSearch(nodeObj);
      } else if (selectedNode) {
        isNodeHighlighted = nodeObj.name === selectedNode.name || edges.some(function(e) {
          return (e.source === selectedNode.name && e.target === nodeObj.name) ||
                 (e.target === selectedNode.name && e.source === nodeObj.name);
        });
        drawBorder = nodeObj.name === selectedNode.name;
      }

      if (hoveredNode && hoveredNode.name === nodeObj.name) {
        isNodeHighlighted = true;
        drawBorder = true;
      }

      var baseAlpha = isNodeHighlighted ? 0.92 : 0.22;
      var strokeAlpha = isNodeHighlighted ? 1.0 : 0.24;
      var fillStyle = alphaColor(nodeObj.fill || 'rgba(0,102,0,0.75)', baseAlpha);
      var strokeStyle = alphaColor(nodeObj.stroke || 'rgba(221,221,221,1.00)', strokeAlpha);

      if (drawBorder) {
        ctx.beginPath();
        ctx.arc(nodeObj.x, nodeObj.y, nodeObj.radius + 7, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(255, 159, 28, 0.24)';
        ctx.fill();
      }

      ctx.beginPath();
      ctx.arc(nodeObj.x, nodeObj.y, nodeObj.radius, 0, Math.PI * 2);
      ctx.fillStyle = fillStyle;
      ctx.fill();
      ctx.lineWidth = drawBorder ? 2.6 : parseNumber(nodeObj.strokewidth || 2);
      ctx.strokeStyle = drawBorder ? '#ff9f1c' : strokeStyle;
      ctx.stroke();

      var shouldLabel = shouldDrawLabel(nodeObj, isNodeHighlighted);
      if (!shouldLabel) return;

      var label = nodeObj.displayName || nodeObj.name;
      var fontSize = nodeObj === hoveredNode || nodeObj === selectedNode ? 12 : 10.5;
      var y = nodeObj.y - nodeObj.radius - 8;
      if (y < 16) y = nodeObj.y + nodeObj.radius + 12;

      ctx.font = 'bold ' + fontSize + 'px sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.lineJoin = 'round';
      ctx.strokeStyle = 'rgba(0,0,0,0.9)';
      ctx.lineWidth = 4;
      ctx.strokeText(label, nodeObj.x, y);
      ctx.fillStyle = isNodeHighlighted ? 'rgba(255,255,255,1)' : 'rgba(255,255,255,0.35)';
      ctx.fillText(label, nodeObj.x, y);
    }

    function shouldDrawLabel(nodeObj, isNodeHighlighted) {
      if (nodeObj === hoveredNode || nodeObj === selectedNode) return true;
      if (searchText && isNodeHighlighted) return true;
      if (selectedNode && isNodeHighlighted && nodeObj.linksCount > 0) return true;
      return Boolean(topLabelNames[nodeObj.name]) && canvasWidth > 520;
    }

    function matchesSearch(nodeObj) {
      return !searchText || nodeObj.searchName.indexOf(searchText) !== -1;
    }

    tick();
  }

  function prepareCanvas(canvas, baseWidth, baseHeight) {
    var rect = canvas.getBoundingClientRect();
    var ratio = baseHeight / baseWidth;
    var width = Math.max(280, Math.round(rect.width || baseWidth));
    var height = Math.max(280, Math.round(rect.height || (width * ratio)));
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

  function loadRemoteMap(url, prefix, ttl, normalizer) {
    if (!url) return Promise.resolve({});

    var cacheKey = prefix + hashString(url);
    var cached = readCachedMap(cacheKey, ttl);
    if (cached) return cached;

    if (mapPromises.has(url)) {
      return mapPromises.get(url);
    }

    var promise = fetch(url, { credentials: 'same-origin' })
      .then(function(res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then(function(payload) {
        var map = normalizer(payload);
        writeCachedMap(cacheKey, map);
        return map;
      });

    mapPromises.set(url, promise);
    return promise;
  }

  function loadCardTranslationMap(baseUrl, names) {
    var uniqueNames = [];
    var seen = {};
    (names || []).forEach(function(name) {
      name = String(name || '').trim();
      if (name && !seen[name]) {
        seen[name] = true;
        uniqueNames.push(name);
      }
    });

    if (!uniqueNames.length) {
      return Promise.resolve({});
    }

    var cacheKey = 'hs_parses_cards_ru_v3_' + hashString(baseUrl + JSON.stringify(uniqueNames));
    var cached = readCachedMap(cacheKey, 7 * 24 * 60 * 60 * 1000);
    if (cached) return cached;
    if (mapPromises.has(cacheKey)) return mapPromises.get(cacheKey);

    var promise = fetch(baseUrl, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ names: uniqueNames })
    })
      .then(function(res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then(function(payload) {
        var map = normalizeCardTranslationPayload(payload);
        writeCachedMap(cacheKey, map);
        return map;
      });

    mapPromises.set(cacheKey, promise);
    return promise;
  }

  function normalizeCardTranslationPayload(payload) {
    var map = {};
    if (!payload) return map;

    var items = payload.items || payload;
    var meta = payload.meta || {};
    if (!items) return map;

    if (!Array.isArray(items) && typeof items === 'object') {
      Object.keys(items).forEach(function(en) {
        if (!en || !items[en]) return;
        var info = meta[en] && typeof meta[en] === 'object' ? meta[en] : {};
        map[normalizeKey(en)] = {
          name: String(info.name_ru || items[en] || '').trim(),
          image: String(info.image || '').trim(),
          image_raw: String(info.image_raw || '').trim(),
          rarity: String(info.rarity || 'common').trim()
        };
      });
      return map;
    }

    if (Array.isArray(items)) {
      items.forEach(function(item) {
        if (!item || typeof item !== 'object') return;
        var en = item.name_en || item.eng || item.source || '';
        var ru = item.name_ru || item.rus || item.translated || '';
        if (!en || !ru) return;
        map[normalizeKey(en)] = {
          name: String(ru).trim(),
          image: String(item.image || '').trim(),
          image_raw: String(item.image_raw || '').trim(),
          rarity: String(item.rarity || 'common').trim()
        };
      });
    }

    return map;
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

  function alphaColor(value, alpha) {
    var color = String(value || '');
    if (/rgba?\(/i.test(color)) {
      if (/rgba\(/i.test(color)) {
        return color.replace(/,\s*[\d.]+\s*\)$/, ', ' + alpha + ')');
      }
      return color.replace(/\)$/, ', ' + alpha + ')').replace(/^rgb/i, 'rgba');
    }
    return color;
  }

  function setupFullscreen(wrapper, onResize) {
    var button = wrapper.querySelector('.hs-graph-fullscreen-btn');
    if (!button) return;

    var openLabel = button.textContent.trim() || 'На весь экран';
    var exitLabel = button.getAttribute('data-exit-label') || 'Закрыть полноэкранный режим';

    function isFullscreen() {
      return document.fullscreenElement === wrapper || wrapper.classList.contains('hs-graph-fullscreen-active');
    }

    function refresh() {
      button.textContent = isFullscreen() ? exitLabel : openLabel;
      window.setTimeout(onResize, 90);
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
    window.addEventListener('resize', debounce(function() {
      onResize();
    }, 120));
  }

  function debounce(fn, delay) {
    var timeout = null;
    return function() {
      window.clearTimeout(timeout);
      timeout = window.setTimeout(fn, delay);
    };
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
      // Local storage is optional; server-side transients still cache the data.
    }
  }

  function normalizeKey(value) {
    return String(value || '').toLowerCase().replace(/\s+/g, ' ').trim();
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

  function escapeAttr(s) {
    return escapeHtml(s).replace(/`/g, '&#096;');
  }
})();
