# Методология построения интерактивного графика распределения метагейма (Winrate / Popularity Scatter Plot)

Интерактивный график распределения метагейма (Scatter Plot) — это мощный инструмент анализа, позволяющий визуализировать положение архетипов в метагейме на двухмерной плоскости. Он наглядно демонстрирует баланс сил в игре:
- **По горизонтальной оси (X)** откладывается процент побед (**Winrate**) — показатель силы и эффективности колоды.
- **По вертикальной оси (Y)** откладывается популярность (**Popularity**) — доля игр на этой колоде в общем объеме матчей.

Этот график позволяет моментально идентифицировать «королей меты» (высокий винрейт и высокая популярность), «скрытых фаворитов» (высокий винрейт, но малая популярность), «оверхайпнутые колоды» (высокая популярность при низком винрейте) и слабые фановые архетипы.

---

## 1. Сбор и обработка данных

Для построения графика используются структурированные данные мета-отчета (например, собираемые с HSGuru или HSReplay).

### Шаг 1. Парсинг параметров
Каждому архетипу на графике соответствует точка. Нам необходимо извлечь два числовых значения из сырых строковых данных:
1. **Винрейт ($W$)**: Например, из строки `"61.1"` или `"61.1%"` мы извлекаем число с плавающей точкой `61.1`.
2. **Популярность ($P$)**: Данные популярности часто приходят в формате `"2.8% (10050)"`, где `2.8%` — доля, а `10050` — количество сыгранных игр. Нам необходимо выделить процентную долю как число `2.8` и (опционально) сохранить размер выборки.

### Шаг 2. Классификация по игровым классам и цветовая кодировка
Для визуального удобства точки окрашиваются в стандартные цвета соответствующих игровых классов Hearthstone. Класс определяется на основе названия архетипа (например, `"No Minion DH"` относим к Demon Hunter, `"Harold DK"` — к Death Knight).

Таблица цветов игровых классов (HEX):
```json
{
  "Death Knight": "#008f7d",
  "Demon Hunter": "#a330c9",
  "Druid": "#ff7d0a",
  "Hunter": "#abd473",
  "Mage": "#40c7eb",
  "Paladin": "#f58cba",
  "Priest": "#ffffff",
  "Rogue": "#fff569",
  "Shaman": "#0070de",
  "Warlock": "#8787ed",
  "Warrior": "#c79c6e",
  "Neutral": "#999999"
}
```

---

## 2. Математическое отображение в экранные координаты (Coordinate Mapping)

Чтобы нарисовать точку на HTML5 Canvas, необходимо перевести математические координаты (проценты побед и популярности) в пиксельные координаты холста.

### Параметры разметки (Layout)
- Ширина холста ($Width$): например, $850$ пикселей.
- Высота холста ($Height$): например, $500$ пикселей.
- Отступы (Padding) для размещения осей, названий и меток шкалы:
  - $Padding_{Left} = 60$ (для шкалы популярности)
  - $Padding_{Right} = 40$ (для запаса названий справа)
  - $Padding_{Top} = 30$
  - $Padding_{Bottom} = 50$ (для шкалы винрейта)

Размер внутренней области графика:
- $Chart_{Width} = Width - Padding_{Left} - Padding_{Right}$
- $Chart_{Height} = Height - Padding_{Top} - Padding_{Bottom}$

### Границы систем координат
- **Винрейт (X)**: устанавливается фиксированный диапазон, характерный для Hearthstone — от $35\%$ до $65\%$. Все точки, выходящие за рамки, отсекаются.
  - $X_{Min} = 35$, $X_{Max} = 65$
- **Популярность (Y)**: шкала начинается с $0\%$, а верхний предел $Y_{Max}$ рассчитывается динамически на основе максимальной популярности среди всех архетипов в выборке (с округлением до ближайшего числа, кратного $5$):
  - $Y_{Min} = 0$, $Y_{Max} = \lceil P_{Max} / 5 \rceil \times 5$

### Формулы перевода в координаты пикселей
Для винрейта $W$:
$$X_{pixel} = Padding_{Left} + \frac{W - X_{Min}}{X_{Max} - X_{Min}} \times Chart_{Width}$$

Для популярности $P$ (обратите внимание, что координата $Y$ в компьютерной графике направлена сверху вниз):
$$Y_{pixel} = Height - Padding_{Bottom} - \frac{P - Y_{Min}}{Y_{Max} - Y_{Min}} \times Chart_{Height}$$

---

## 3. Алгоритм интерактивного рендеринга на Canvas

Для высокой четкости и отзывчивости рендеринг выполняется на HTML5 Canvas за несколько проходов.

### Этап 1. Отрисовка фона и сетки
1. Заливка фона темным цветом (например, `#1e1e24` для комфортного контраста).
2. Отрисовка горизонтальных линий сетки с шагом по Y (обычно $2\%$ или $5\%$). Для каждой линии рассчитывается координата $Y_{pixel}$, проводится тонкая линия (`#2d2d38`) и пишется текст значения (например, `"10%"`).
3. Отрисовка вертикальных линий сетки с шагом по X (каждые $5\%$). Проводятся линии и подписываются значения винрейта на оси абсцисс.
4. Отрисовка сплошных линий осей координат толщиной 2px.

### Этап 2. Двухпроходная отрисовка точек
Чтобы избежать перекрытия активных (подсвеченных) элементов другими точками, применяется **двухпроходный рендеринг**:
1. **Первый проход**: Отрисовка всех точек, кроме той, на которую в данный момент наведен курсор мыши.
2. **Второй проход**: Отрисовка hovered-точки поверх всех остальных.

### Этап 3. Рендеринг индивидуальной точки
Каждая точка рисуется в виде заполненного круга:
1. Заливка круга цветом класса радиусом 5px (7px при наведении).
2. Обводка круга рамкой (черной полупрозрачной для обычных точек, ярко-белой толщиной 2px — при наведении).
3. Для hovered-точки подкладывается дополнительный размытый круг радиального градиента или полупрозрачный белый ореол большего радиуса (10px) для эффекта свечения.
4. Вывод текста названия архетипа со смещением вправо на 8px. Чтобы текст легко читался поверх сетки и других точек, накладывается тень текста:
   ```javascript
   ctx.shadowColor = "rgba(0, 0, 0, 0.95)";
   ctx.shadowBlur = 4;
   ctx.shadowOffsetX = 1;
   ctx.shadowOffsetY = 1;
   ```

---

## 4. Интерактивность и детекция наведения (Hover Detection)

Так как Canvas рисует растровое изображение, в нем нет отдельных DOM-объектов для точек. Определение наведения курсора реализуется математически.

При движении мыши по холсту считываются координаты курсора $(X_{mouse}, Y_{mouse})$ относительно элемента:
```javascript
const rect = canvas.getBoundingClientRect();
const mouseX = (event.clientX - rect.left) * (canvas.width / rect.width);
const mouseY = (event.clientY - rect.top) * (canvas.height / rect.height);
```

Далее для каждой точки рассчитывается Евклидово расстояние до курсора:
$$Distance = \sqrt{(X_{mouse} - X_{pixel})^2 + (Y_{mouse} - Y_{pixel})^2}$$

Если минимальное расстояние среди всех точек меньше порога чувствительности (например, $15$ пикселей), то:
1. Точка объявляется `hoveredPoint`.
2. Курсор мыши меняется на `pointer`.
3. В специальный блок под графиком выводится расширенная информация (название, винрейт, популярность, количество сыгранных матчей, средняя длительность матча, скорость продвижения в ранке).
4. Запускается перерисовка холста.

---

## 5. Полный рабочий пример кода (HTML + JS)

Вы можете использовать этот пример для мгновенного развертывания интерактивного Scatter Plot:

```html
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <title>Hearthstone Meta Distribution</title>
  <style>
    body {
      background: #0f0f12;
      color: #f0f0f0;
      font-family: sans-serif;
      padding: 20px;
    }
    .chart-box {
      background: #111113;
      border: 1px solid #2d2d38;
      border-radius: 8px;
      padding: 15px;
      max-width: 850px;
      margin: 0 auto;
    }
    #tooltip {
      min-height: 40px;
      background: rgba(255, 255, 255, 0.03);
      border: 1px solid #2d2d38;
      border-radius: 6px;
      padding: 10px;
      text-align: center;
      margin-top: 15px;
      font-size: 0.95rem;
    }
  </style>
</head>
<body>

<div class="chart-box">
  <canvas id="metaScatter" width="850" height="500" style="width: 100%; aspect-ratio: 850/500; display: block;"></canvas>
  <div id="tooltip">Наведите курсор на точку архетипа для просмотра данных</div>
</div>

<script>
  const canvas = document.getElementById("metaScatter");
  const ctx = canvas.getContext("2d");
  const tooltip = document.getElementById("tooltip");

  // Исходные данные (пример)
  const dataset = [
    { name: "No Minion DH", winrate: 61.1, popularity: 2.8, games: 10050, hsClass: "Demon Hunter" },
    { name: "Token Druid", winrate: 60.6, popularity: 4.7, games: 16686, hsClass: "Druid" },
    { name: "End of Turnadin", winrate: 59.7, popularity: 3.5, games: 12368, hsClass: "Paladin" },
    { name: "No Hand Hunter", winrate: 59.2, popularity: 10.1, games: 35986, hsClass: "Hunter" },
    { name: "Harold Shaman", winrate: 58.8, popularity: 8.8, games: 31086, hsClass: "Shaman" },
    { name: "Companion Hunter", winrate: 54.2, popularity: 21.8, games: 77327, hsClass: "Hunter" },
    { name: "Control Priest", winrate: 46.4, popularity: 7.2, games: 25526, hsClass: "Priest" },
    { name: "Krona Druid", winrate: 38.0, popularity: 0.5, games: 1719, hsClass: "Druid" }
  ];

  const CLASS_COLORS = {
    "Death Knight": "#008f7d", "Demon Hunter": "#a330c9", "Druid": "#ff7d0a",
    "Hunter": "#abd473", "Mage": "#40c7eb", "Paladin": "#f58cba",
    "Priest": "#ffffff", "Rogue": "#fff569", "Shaman": "#0070de",
    "Warlock": "#8787ed", "Warrior": "#c79c6e", "Neutral": "#999999"
  };

  const padLeft = 60, padRight = 40, padTop = 30, padBottom = 50;
  const chartW = canvas.width - padLeft - padRight;
  const chartH = canvas.height - padTop - padBottom;

  const xMin = 35, xMax = 65;
  const yMin = 0;
  const maxVal = Math.max(...dataset.map(d => d.popularity), 5);
  const yMax = Math.ceil(maxVal / 5) * 5;

  const mapX = (wr) => padLeft + ((wr - xMin) / (xMax - xMin)) * chartW;
  const mapY = (pop) => canvas.height - padBottom - ((pop - yMin) / (yMax - yMin)) * chartH;

  let hoveredPoint = null;

  function draw() {
    ctx.fillStyle = "#1e1e24";
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    // Сетка и Оси
    ctx.strokeStyle = "#2d2d38";
    ctx.lineWidth = 1;
    ctx.fillStyle = "#a0a0b0";
    ctx.font = "11px sans-serif";

    // Горизонтальные линии (Y)
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    for (let pop = yMin; pop <= yMax; pop += 5) {
      const y = mapY(pop);
      ctx.beginPath(); ctx.moveTo(padLeft, y); ctx.lineTo(canvas.width - padRight, y); ctx.stroke();
      ctx.fillText(pop + "%", padLeft - 8, y);
    }

    // Вертикальные линии (X)
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    for (let wr = xMin; wr <= xMax; wr += 5) {
      const x = mapX(wr);
      ctx.beginPath(); ctx.moveTo(x, padTop); ctx.lineTo(x, canvas.height - padBottom); ctx.stroke();
      ctx.fillText(wr + "%", x, canvas.height - padBottom + 8);
    }

    // Отрисовка точек
    dataset.forEach(p => { if (p !== hoveredPoint) drawPoint(p, false); });
    if (hoveredPoint) drawPoint(hoveredPoint, true);
  }

  function drawPoint(p, isHovered) {
    const cx = mapX(p.winrate);
    const cy = mapY(p.popularity);
    const color = CLASS_COLORS[p.hsClass] || CLASS_COLORS["Neutral"];

    if (isHovered) {
      ctx.beginPath(); ctx.arc(cx, cy, 10, 0, 2 * Math.PI);
      ctx.fillStyle = "rgba(255, 255, 255, 0.25)"; ctx.fill();
    }

    ctx.beginPath(); ctx.arc(cx, cy, isHovered ? 7 : 5, 0, 2 * Math.PI);
    ctx.fillStyle = color; ctx.fill();
    ctx.strokeStyle = isHovered ? "#fff" : "rgba(0, 0, 0, 0.5)";
    ctx.lineWidth = isHovered ? 2 : 1; ctx.stroke();

    ctx.font = isHovered ? "bold 12px sans-serif" : "10px sans-serif";
    ctx.fillStyle = isHovered ? "#fff" : "#cccccc";
    ctx.textAlign = "left"; ctx.textBaseline = "middle";

    ctx.shadowColor = "rgba(0, 0, 0, 0.95)"; ctx.shadowBlur = isHovered ? 6 : 4;
    ctx.shadowOffsetX = 1; ctx.shadowOffsetY = 1;
    ctx.fillText(p.name, cx + 8, cy);
    ctx.shadowColor = "transparent"; ctx.shadowBlur = 0;
  }

  canvas.addEventListener("mousemove", (e) => {
    const rect = canvas.getBoundingClientRect();
    const mouseX = (e.clientX - rect.left) * (canvas.width / rect.width);
    const mouseY = (e.clientY - rect.top) * (canvas.height / rect.height);

    let found = null;
    let minDistance = 15;

    for (const p of dataset) {
      const cx = mapX(p.winrate);
      const cy = mapY(p.popularity);
      const dist = Math.hypot(mouseX - cx, mouseY - cy);
      if (dist < minDistance) {
        minDistance = dist;
        found = p;
      }
    }

    if (found !== hoveredPoint) {
      hoveredPoint = found;
      canvas.style.cursor = hoveredPoint ? "pointer" : "default";
      if (hoveredPoint) {
        tooltip.innerHTML = `
          <strong style="color: ${CLASS_COLORS[hoveredPoint.hsClass]}">${hoveredPoint.name}</strong> · 
          Винрейт: <span style="color: #4cd137; font-weight: bold;">${hoveredPoint.winrate}%</span> · 
          Популярность: <span style="color: #00a8ff; font-weight: bold;">${hoveredPoint.popularity}%</span> 
          (${hoveredPoint.games} игр)
        `;
      } else {
        tooltip.textContent = "Наведите курсор на точку архетипа для просмотра данных";
      }
      draw();
    }
  });

  draw();
</script>
</body>
</html>
```

---

## 6. Преимущества Canvas перед SVG/DOM

1. **Неограниченное число точек**: При анализе сотен версий колод и архетипов (например, при детализированных фильтрах) Canvas сохраняет стабильные **60 FPS** за счет отсутствия накладных расходов на создание сотен DOM-узлов.
2. **Абсолютный графический контроль**: Свойства тени, ореола, кастомных шрифтов и укрупнения точек при наведении полностью контролируются кодом на каждом кадре, исключая задержки отрисовки CSS-переходов.
3. **Единый растровый холст**: График можно сохранить как изображение (через `canvas.toDataURL()`), что позволяет пользователям скачивать сгенерированную инфографику.
