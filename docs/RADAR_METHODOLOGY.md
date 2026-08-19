# Методология создания интерактивного радара синергии карт (Data Reaper's Radar)

Интерактивный радар — это инструмент визуализации метагейма, представляющий собой сетевой граф связей между картами. Он позволяет наглядно увидеть, какие карты чаще всего используются вместе в колодах конкретного класса, какие образуют устойчивые синергии («ядра» архетипов), а какие являются гибкими техническими решениями.

В данном документе подробно описано, как устроен этот инструмент: от математического сбора и обработки данных до построения физической симуляции графа в браузере.

---

## 1. Сбор и математическая обработка данных

В основе радара лежит анализ совместной встречаемости карт в большом массиве колод (базе данных матчей или трекеров).

### Шаг 1. Расчет популярности карт (Узлы графа)
Каждому узлу (карте) соответствует радиус, пропорциональный её популярности внутри класса.

$$R_i = K_{min} + C \cdot P_i$$

Где:
- $P_i$ — популярность карты (доля колод класса, содержащих карту $i$, от $0.0$ до $1.0$).
- $C$ — масштабирующий коэффициент (например, $25.0$).
- $K_{min}$ — минимальный радиус узла для отображения коротких названий (например, $8.0$).

### Шаг 2. Расчет силы связи между картами (Ребра графа)
Связи (ребра) между картами определяются на основе их **совместной встречаемости**. Если две карты часто лежат в одной и той же колоде, между ними формируется сильная связь (большой вес).

Существует несколько подходов к расчету веса связи $W_{ij}$ между картами $i$ и $j$:

1. **Коэффициент сходства Жаккара (Jaccard Similarity Index):**
   Определяет отношение пересечения множеств колод к их объединению.
   $$W_{ij} = \frac{N(i \cap j)}{N(i) + N(j) - N(i \cap j)}$$
   *Где $N(i)$ — число колод с картой $i$, а $N(i \cap j)$ — число колод, содержащих обе карты.*

2. **Индекс условной вероятности (Conditional Probability):**
   Часто оценивается как вероятность встретить карту $j$, если в колоде уже есть карта $i$.
   
3. **Корреляция Пирсона (Pearson Correlation):**
   Позволяет учесть случаи, когда карты заменяют друг друга (отрицательная корреляция) или дополняют (положительная корреляция).

На практике Vicious Syndicate использует нормализованные веса совместной встречаемости, отсекая слишком слабые связи ($W_{ij} < 0.1$), чтобы не загромождать граф фоновым шумом.

---

## 2. Структура данных (JSON)

Результатом обработки является JSON-структура, которая передается на фронтенд для рендеринга:

```json
{
  "class": "DeathKnight",
  "archetype": "Plague Death Knight",
  "title": "Data Reaper's Radar - Issue #349 - Plague Death Knight",
  "issue": "349",
  "url": "https://www.vicioussyndicate.com/deck-library/death-knight-decks/plague-death-knight/",
  "radar_url": "https://www.vicioussyndicate.com/wp-content/datareaper/radars/DeathKnight/index.html",
  "deck_code": "AAECAfHhBAam6wX7+AWYgQaHggaOgAbTngYM88gF8+gFlMoF16IGl6UGmKUGmqUG6aUGkLUGkrUEl7UEAAA=",
  "nodes": [
    {
      "name": "Arisen Onyxia",
      "radius": 14.6,
      "strokewidth": 2.0,
      "fill": "rgba(0,102,0,0.75)",
      "stroke": "rgba(255,127,0,1.00)",
      "text": "rgba(255,255,255,1.00)"
    },
    {
      "name": "Hematurge",
      "radius": 22.1,
      "strokewidth": 2.0,
      "fill": "rgba(0,102,0,0.75)",
      "stroke": "rgba(0,112,221,1.00)",
      "text": "rgba(255,255,255,1.00)"
    }
  ],
  "edges": [
    {
      "source": "Arisen Onyxia",
      "target": "Hematurge",
      "weight": 0.31,
      "length": 250.0,
      "stroke": "rgba(0,0,0,0.03)"
    }
  ]
}
```

- **Параметр `archetype`** содержит название конкретного субарехтипа класса (или `null` для общего радара класса).
- **Параметр `deck_code`** содержит прямой импортируемый код колоды в Hearthstone для быстрого импорта.
- **Узлы (nodes)** хранят визуальные атрибуты: радиус, цвета заливки (`fill` кодирует тип карты или её школу магии / руны) и рамки (`stroke` кодирует редкость или класс карты).
- **Ребра (edges)** связывают узлы по именам и имеют вес (`weight`), определяющий упругость связи в симуляции, и желаемую длину пружины (`length`).

---

## 3. Физический алгоритм графа (Force-Directed Graph)

Для красивого распределения узлов на плоскости используется алгоритм **физической симуляции упругих сил (Force-Directed Layout)**. В рамках каждого кадра анимации (`requestAnimationFrame`) на узлы действуют три силы:

### 1. Сила отталкивания узлов (Закон Кулона)
Все узлы ведут себя как одноименно заряженные частицы, отталкиваясь друг от друга. Это предотвращает наложение кругов.

Для каждой пары узлов $A$ и $B$:
- Расстояние между ними: $d = \sqrt{\Delta x^2 + \Delta y^2}$
- Минимально допустимое расстояние: $d_{min} = R_A + R_B + Margin$ (где $Margin \approx 30$–$40$ пикселей).
- Если расстояние $d < d_{min}$, рассчитывается сила отталкивания $F_{repel} = (d_{min} - d) \cdot K_{repel}$.
- Компоненты ускорения узла $A$ уменьшаются, а узла $B$ увеличиваются пропорционально направлению вектора связи.

### 2. Сила притяжения связей (Закон Гука для пружин)
Ребра графа ведут себя как пружины. Если связанные узлы находятся дальше желаемой длины $L_0$ (обычно $220$–$250$ пикселей), пружина стягивает их вместе. Если ближе — расталкивает.

Сила натяжения пружины пропорциональна весу связи (синергии):
$$F_{attract} = (d - L_0) \cdot W_{ij} \cdot K_{attract}$$

- Сильные синергии (большой $W_{ij}$) стягивают узлы очень близко, образуя плотные кластеры карт, играющих в одной колоде.
- Слабые синергии почти не влияют на координаты карт.

### 3. Сила гравитации (Притяжение к центру)
Чтобы граф не разлетался за пределы экрана и не уплывал, на каждый узел действует слабая сила притяжения к геометрическому центру холста $(X_c, Y_c)$ (например, точка $375, 375$ на холсте $750 \times 750$).
$$F_{grav} = d_{center} \cdot K_{grav}$$

### Трение и затухание скорости (Damping)
Чтобы симуляция стабилизировалась и не колебалась бесконечно, в конце каждого такта скорость узлов умножается на коэффициент трения (затухания) $D \approx 0.85$.
$$V_{new} = (V_{old} + F) \cdot D$$

---

## 4. Пример кода реализации симуляции (JavaScript)

Ниже приведена упрощенная реализация симуляции и рендеринга на элементе `HTML5 <canvas>`:

```javascript
// Константы симуляции
const WIDTH = 750;
const HEIGHT = 750;
const CENTER_X = WIDTH / 2;
const CENTER_Y = HEIGHT / 2;

function tick() {
  // 1. Сила гравитации к центру холста
  for (const n of nodes) {
    const dx = CENTER_X - n.x;
    const dy = CENTER_Y - n.y;
    const dist = Math.sqrt(dx * dx + dy * dy) || 1;
    n.vx += (dx / dist) * 0.15;
    n.vy += (dy / dist) * 0.15;
  }

  // 2. Сила отталкивания между узлами (Кулон)
  for (let i = 0; i < nodes.length; i++) {
    const n1 = nodes[i];
    for (let j = i + 1; j < nodes.length; j++) {
      const n2 = nodes[j];
      const dx = n2.x - n1.x;
      const dy = n2.y - n1.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const minDist = n1.radius + n2.radius + 35; // радиусы + отступ
      if (dist < minDist) {
        const force = (minDist - dist) * 0.4;
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;
        n1.vx -= fx;
        n1.vy -= fy;
        n2.vx += fx;
        n2.vy += fy;
      }
    }
  }

  // 3. Сила притяжения ребер (Закон Гука)
  for (const e of edges) {
    const sourceNode = nodes.find(n => n.name === e.source);
    const targetNode = nodes.find(n => n.name === e.target);
    if (sourceNode && targetNode) {
      const dx = targetNode.x - sourceNode.x;
      const dy = targetNode.y - sourceNode.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      
      if (e.weight > 0.1) {
        const desiredLen = e.length || 220;
        const k = 0.015 * e.weight; // жесткость пружины зависит от силы связи
        const force = (dist - desiredLen) * k;
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;
        sourceNode.vx += fx;
        sourceNode.vy += fy;
        targetNode.vx -= fx;
        targetNode.vy -= fy;
      }
    }
  }

  // 4. Применение скоростей с учетом трения и границ
  for (const n of nodes) {
    if (n === draggedNode) continue; // игнорируем узел, перетаскиваемый мышью

    n.vx *= 0.85; // трение
    n.vy *= 0.85;

    n.x += n.vx;
    n.y += n.vy;

    // Ограничение границами холста
    const margin = n.radius + 10;
    if (n.x < margin) { n.x = margin; n.vx = -n.vx * 0.5; }
    if (n.x > WIDTH - margin) { n.x = WIDTH - margin; n.vx = -n.vx * 0.5; }
    if (n.y < margin) { n.y = margin; n.vy = -n.vy * 0.5; }
    if (n.y > HEIGHT - margin) { n.y = HEIGHT - margin; n.vy = -n.vy * 0.5; }
  }

  // 5. Отрисовка кадра на холсте
  drawCanvas();

  requestAnimationFrame(tick);
}
```

---

## 5. Сборка собственного радара на основе своей статистики

Если вы хотите построить подобный радар для своей локальной базы колод (например, спарсенной с Firestone или полученной из трекера), воспользуйтесь следующим скриптом на Python. Скрипт принимает на вход список колод (в виде списков названий карт) и преобразует их в JSON-файл для радара.

```python
import json
import math
from collections import Counter, defaultdict

def build_radar_data(decks: list[list[str]], min_occurrences: int = 5, min_cooccurrence_weight: float = 0.12) -> dict:
    """
    decks: список колод, где каждая колода — это список названий карт (без дубликатов для чистоты синергии)
    """
    total_decks = len(decks)
    if total_decks == 0:
        return {"nodes": [], "edges": []}

    # 1. Считаем популярность карт
    card_counts = Counter()
    for deck in decks:
        card_counts.update(deck)

    # Отфильтруем слишком редкие карты
    frequent_cards = {card: count for card, count in card_counts.items() if count >= min_occurrences}
    
    # Считаем совместную встречаемость
    cooccurrence = defaultdict(int)
    for deck in decks:
        valid_cards = [c for c in deck if c in frequent_cards]
        # Перебираем уникальные пары в колоде
        for i in range(len(valid_cards)):
            for j in range(i + 1, len(valid_cards)):
                c1, c2 = sorted([valid_cards[i], valid_cards[j]])
                cooccurrence[(c1, c2)] += 1

    # 2. Создаем узлы (Nodes)
    nodes = []
    for card, count in frequent_cards.items():
        popularity = count / total_decks
        # Масштабируем радиус от 8.0 до 25.0 пикселей
        radius = 8.0 + (popularity * 22.0)
        
        # Задаем цвета по умолчанию (можно кастомизировать по классам или типам карт)
        nodes.append({
          "name": card,
          "radius": round(radius, 1),
          "strokewidth": 2.0,
          "fill": "rgba(0, 102, 0, 0.75)",      # Зеленый фон для карт класса
          "stroke": "rgba(221, 221, 221, 1.0)",  # Обычная серая рамка
          "text": "rgba(255, 255, 255, 1.0)"
        })

    # 3. Создаем ребра (Edges)
    edges = []
    for (c1, c2), intersection_count in cooccurrence.items():
        # Расчет веса Жаккара
        union_count = frequent_cards[c1] + frequent_cards[c2] - intersection_count
        jaccard_weight = intersection_count / union_count
        
        if jaccard_weight >= min_cooccurrence_weight:
            # Округляем до сотых
            edges.append({
                "source": c1,
                "target": c2,
                "weight": round(jaccard_weight, 2),
                "length": 250.0,
                "stroke": f"rgba(0, 0, 0, {round(jaccard_weight * 0.2, 2)})"
            })

    return {
        "nodes": nodes,
        "edges": edges
    }

# Пример использования:
my_decks = [
    ["Sylvanas", "Ragnaros", "Azure Drake", "Defender of Argus"],
    ["Sylvanas", "Ragnaros", "Azure Drake", "Sludge Belcher"],
    ["Azure Drake", "Defender of Argus", "Knife Juggler"],
    ["Sylvanas", "Sludge Belcher", "Defender of Argus"],
    ["Ragnaros", "Azure Drake", "Sludge Belcher", "Knife Juggler"]
]

radar_json = build_radar_data(my_decks, min_occurrences=2, min_cooccurrence_weight=0.1)
print(json.dumps(radar_json, indent=2, ensure_ascii=False))
```

---

## 6. Преимущества визуализации через Canvas перед SVG/DOM

При отображении графов синергии карт количество элементов может быстро расти (например, 36 карт образуют до 500-600 сильных связей).
- **DOM/SVG:** Отрисовка сотен тегов `<line>` и `<circle>` и изменение их координат через CSS или JS на каждом кадре создает огромную нагрузку на графический поток браузера, приводя к просадкам FPS.
- **HTML5 Canvas:** Отрисовывает все элементы на одном растровом слое за один проход очистки и перерисовки. Это гарантирует плавную работу симуляции с частотой **60 кадров в секунду** даже на слабых мобильных устройствах.
