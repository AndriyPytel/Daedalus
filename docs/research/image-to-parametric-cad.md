# Image / sketch / point-cloud → ПАРАМЕТРИЧНИЙ CAD: хто це вміє і що з цього можна взяти

*Огляд літератури і продуктів, тільки первинні джерела: arXiv (abstract + метод + обмеження),
GitHub README/LICENSE/код, model card на HuggingFace, PyPI-метадані, офіційні docs. Кожне
твердження несе URL джерела, що ним володіє. Факти, які я перевірив сам у цьому запуску,
позначені **[виміряно]**. Дата зрізу: серпень 2026.*

Це продовження `drawing-understanding-literature.md`. Там питання було «як прочитати креслення».
Тут — «а чи є готова AI-система, що з зображення будує 3D, і чи можна на її основі зробити
**параметричну** модель». Попередній вирок у `README.md` («усі генеративні CAD-моделі видають
нормалізовані квантовані координати») — **підтверджено первинними джерелами і посилено
цифрами**; але два його висновки треба **уточнити** (див. §0.1).

---

## 0. Відповідь на питання одним абзацом

AI, що з зображення будує 3D-об'єкт, є, і їх десятки. Вони діляться на дві незв'язані сім'ї.
Перша (TripoSR/TRELLIS/Hunyuan3D) видає **меш** — розібрано в `README.md` §4, тут не
повторюється. Друга, яка нас і цікавить, видає **CAD-послідовність або Python-код**, і саме вона
називає себе «parametric CAD generation». Станом на серпень 2026 у цій другій сім'ї:
**вихід читабельний і виконуваний — так; вихід метричний — ні; вихід з іменованими параметрами
і constraint-ами — ні в жодної системи, крім однієї (CAD-Assistant), і та не офлайн і
некомерційна.** Придатне до реюзу з цієї сім'ї — **не ваги і не моделі, а три речі:
формат виходу (виконуваний код замість токенів), verify-loop через ядро, і словник
constraint-ів із SketchGraphs**.

### 0.1 Що уточнюємо у попередньому README

| Твердження в `README.md` | Статус після цієї перевірки |
|---|---|
| «Усі генеративні CAD-моделі видають нормалізовані квантовані координати — 256 рівнів на одиничний куб ≈ 1 мм на деталі 200 мм» | **Підтверджено дослівною цитатою** з DeepCAD §3.1.2 і CAD-Recode App. B, плюс перевірено кодом і датасетом [виміряно]. Уточнення: у CAD-Recode це не 256 рівнів, а сітка −100…+100 з кроком 1 → **1.0 мм** на 200-мм деталі; у DeepCAD з урахуванням `NORM_FACTOR = 0.75` — **≈1.04 мм**, а не 0.78 мм |
| «Найкраща профільна робота — Drawing2CAD, 76 % точності параметрів» | **Застаріло.** З'явився **Ortho2CAD** (arXiv:2607.08891, липень 2026, CMU): растрові ортопроєкції **з розмірами** → CadQuery-код, 100 % валідного коду, IoU 0.7922 на DeepCAD. І **код тепер опублікований** — <https://github.com/AdityaJoglekar/Ortho2CAD> [виміряно: README і HF-датасет доступні], хоча **LICENSE-файлу в репо немає** |
| «Готового рішення немає» | **Підтверджено**, але формулювання уточнюється: готового рішення немає *для метричності й редагованості*. Для «вгадати топологію з форми» рішення вже цілком робочі (IoU 0.92 на DeepCAD) |

---

## 1. Наскрізні критерії: зведена таблиця

Легенда: **(в)** — квантування; **(г)** — метричні мм; **(д)** — редаговані параметри;
**(е)** — ліцензія коду / ваг; **(ж)** — офлайн+pip; **(з)** — точність.

| Система | Вхід | Вихід (б) | Квант. (в) | мм (г) | Живі параметри (д) | Ліцензія код / ваги (е) | Офлайн pip (ж) | Точність (з) |
|---|---|---|---|---|---|---|---|---|
| **CAD-Recode** 2412.14042 | 256-pt хмара | CadQuery Python | −100…+100, крок 1 = **1.0 мм@200мм** | ні | ні (літерали) | **CC BY-NC** / **CC BY-NC** | ні (CUDA-Docker) | DeepCAD IoU 92.0, IR 0.4 %; CC3D IoU 74.2 |
| **cadrille** 2505.22914 | хмара / 4 види / текст | CadQuery Python | те саме | ні | ні | Apache-2.0 / **CC BY-NC** | ні | DeepCAD IoU 90.2, **IR 0.0**; CC3D 67.9 |
| **CAD-Coder** 2505.14646 | 1 зображення | CadQuery Python | масштаб узагалі не вхід | **ні взагалі** | ні | Apache-2.0 / не вказано | ні (4×H100 train) | **VSR 100 %**, IoU_best 0.675 |
| **Ortho2CAD** 2607.08891 | растр. ортокреслення **з розмірами** | CadQuery Python | немає | **частково** (bbox) | так (код) | **LICENSE відсутній** / Qwen3-VL-8B | ні (8B) | 100 % valid; IoU **0.792** / Fusion360 **0.560** |
| **CAD-Assistant** 2412.13810 | текст+зобр.+скан | **FreeCAD Python API** | немає | так (одиниці FreeCAD) | **так: constraint-и + солвер** | CC BY-NC / — (GPT-4o) | **ні — потрібен OpenAI key** | PF1 0.979 / **CF1 0.484**; tool-use 98.5 % |
| **DeepCAD** 2105.09492 | латент / хмара | токени послідовності | 2×2×2, 256 рівнів = **≈1.04 мм@200мм** | ні | sketch+extrude, без constraint | MIT / — | ні (conda+CUDA) | IR 2.72 %, ACC_param 97.98 |
| **Text2CAD** 2409.17106 | текст | токени послідовності | 8 біт / 256 | ні | ні | CC BY-NC-SA / CC BY-NC-SA | ні | med CD 0.37, IR 0.93 %, **F1 дуг 36 %** |
| **Drawing2CAD** 2508.18733 | **векторне** креслення, 4 види | токени DeepCAD | так (257 one-hot) | ні | ні | MIT / — | ні (CUDA 11.8) | ACC_param 76.1, **IR 20.3 %** |
| **Img2CAD** 2410.03417 | зображення/скетч → SVG-wireframe | токени DeepCAD | так | ні | ні | не заявлено | ні (4×A100) | ACC_param 68.8, **IR 28.8 %** |
| **CAD-MLLM** 2411.04954 | текст/зобр./хмара | токени послідовності | 256 рівнів у [−1,1]³ | ні | ні | код не викладено / дані MIT | ні (16×H800) | Chamfer 1.85, SIR 1.31 %, DangEL 0.46 |
| **Free2CAD** (SIGGRAPH'22) | штрихи від руки | CAD-команди | немає (unit bbox) | ні | так | MIT | ні (TF+GPU) | на ≥5 операцій пояснює **половину штрихів** |
| **Sketch2CAD** 2009.04927 | штрих у контексті | 1 CAD-операція | немає | ні | так | MIT | ні (0.07 с на GPU) | Face IoU 78–92 %, класиф. оператора 99.8 % |
| **Point2Cyl** 2112.09329 | хмара 8192 pt | extrusion cylinders | нормалізація в unit sphere | ні | параметри так, **скетч = хмара точок** | MIT | ні | 0.25 с/модель; % валідних B-rep не виміряно |
| **SECAD-Net** 2303.10613 | воксель 64³ | implicit sketch + extrude | немає | ні | так, #P = 4.30 | MIT | ні (**3 хв/форма** fine-tune) | CD 0.330 (ABC), CD 2.052 (Fusion360) |
| **ComplexGen** 2205.14573 | хмара | **B-rep chain complex** | немає (unit cube) | ні | **ні** (без історії) | MIT | ні (**545 с** ILP на модель) | F-score 80.8/80.1, topo-inconsistency **0** |
| **UV-Net** 2006.10211 | B-rep | класи / сегментація | UV-сітка 10×10 | так | ні (тільки мітки) | **MIT** | pythonOCC | MFCAD IoU 99.87 |
| **BRepNet** 2104.00706 | B-rep | сегментація граней | немає | так | ні | **CC BY-NC-SA** | pythonOCC | acc 92.5, IoU 77.1 |
| **BlenderLLM** 2412.14203 | текст | bpy → **меш** | немає | так, без гарантій | ні | Apache-2.0 / **Apache-2.0** | 7B офлайн | E_syntax 31.4 %→**3.4 %** |
| **Zoo Text-to-CAD** (комерція) | текст | **KCL-код + STEP** | немає | **так, юніти першокласні** | так (KCL) | MIT (тулінг) / хмара | **ні — геометричне ядро в хмарі** | метрик не публікують |

---

## 2. Головний факт: квантування, і що воно означає в мм

Це не деталь реалізації, а **конструктивна межа** всієї сім'ї.

### 2.1 DeepCAD — джерело представлення для 80 % робіт

Дослівно, arXiv:2105.09492 §3.1.2 (<https://arxiv.org/abs/2105.09492>):

> «we normalize every CAD model within a **2×2×2 cube**; we also normalize every sketch profile
> within its bounding box, and include a scale factor *s* (in extrusion command) to restore the
> normalized profile into its original size. The normalization restricts the ranges of continuous
> parameters, allowing us to **quantize their values into 256 levels and express them using 8-bit
> integers**.»

Код підтверджує і **уточнює в гірший бік** (`cadlib/macro.py`, `cadlib/extrude.py` у
<https://github.com/ChrisWu1997/DeepCAD>): `NORM_FACTOR = 0.75`, `ARGS_DIM = 256`,
`scale = size * NORM_FACTOR / max(abs(bbox))`.

| крок | обчислення | результат |
|---|---|---|
| нормалізація | півгабарит 100 мм → 0.75 норм. од. | 1 норм. од. = 133.3 мм |
| квантування 3D | 2/256 = 0.0078 норм. од. | **≈1.04 мм на рівень** |
| квантування профілю скетчу | півбокс → 95 рівнів сітки 256 | **≈1.05 мм на рівень** |
| допуск метрики ACC_param | η = 3 рівні | **≈3 мм** вважається «правильною відповіддю» |

Інші ліміти представлення: ≤10 екструзій, ≤6 loop на скетч, ≤15 кривих на loop,
довжина послідовності N_c = 60. Датасет — **178 238 моделей**; «most CAD command sequences
are no longer than 40 or use less than 8 extrusions». Line+Arc+Circle = **92 % усіх команд**;
ні fillet, ні chamfer, ні revolve, ні pattern.

### 2.2 CAD-Recode — те саме, іншими числами

arXiv:2412.14042v2, Appendix B (<https://arxiv.org/html/2412.14042v2>), дослівно:

> «Each resulting model undergoes **normalization to fit within a unit bounding box** centered at
> the origin. The parameters are **quantized so that the coordinates of any point on the CAD
> surface are within the range −100 to 100 with a minimum resolution of 1 unit**.»

CHANGELOG репо: у v1 діапазон був −50…+50, у v1.5 став −100…+100
(<https://github.com/filaPro/cad-recode/blob/main/CHANGELOG.md>). Отже роздільність
**1/200 габариту = 0.5 %**: 1.0 мм на 200-мм деталі, 2.0 мм у v1, 5 мм на деталі 1000 мм.

**[виміряно]** Реальний вихід моделі з їхнього ж `demo.ipynb`
(<https://github.com/filaPro/cad-recode/blob/main/demo.ipynb>, збережений output комірки) —
усі числа цілі в [−100, 100], жодної іменованої змінної, жодної одиниці:

```python
import cadquery as cq
w0=cq.Workplane('XY',origin=(0,0,88))
r=w0.sketch().segment((-98,-100),(-77,-99)).arc((-78,-89),(-77,-79)).segment((-98,-79)).close()
  .assemble()... .finalize().extrude(-177).union(w0.workplane(offset=-15/2).box(196,200,15))
```

Папір прямо визнає наслідок (Fig. 14): «Invalid predictions happen when the CAD model contains
features of **dimension smaller than the resolution induced by quantization**… CAD-Recode
predicts an extrusion distance of 0 as a quantized approximation, resulting in an **invalid CAD
model**». Тобто тонка стінка не просто округлюється — вона **вбиває тіло**.

### 2.3 Це видно навіть у «код-датасетах» — [виміряно]

GenCAD-Code (163k пар «зображення ↔ CadQuery», база для CAD-Coder і для SFT в Ortho2CAD),
<https://huggingface.co/datasets/CADCODER/GenCAD-Code>. Я витягнув перші 100 рядків через
HF datasets-server і порахував числа в коді:

- **5313 числових літералів; 81.8 % з них — точні кратні 1/256.** Це квантування DeepCAD,
  просто записане десятковим дробом.
- максимальний модуль координати — 9.0, типові габарити 0.17…0.75 → **безрозмірний масштаб**;
- **0 присвоєнь виду `name = число`** у всіх 100 скриптах. Змінні є тільки для `cq.Workplane`;
- типовий літерал: `0.17730263157894738`. Це не «параметр», це **артефакт ділення на 256**.

Тобто корпус, на якому вчать «editable CAD code», сам не містить жодного редагованого параметра.
Модель не може навчитися тому, чого немає в даних.

### 2.4 Зведення по квантуванню

| Робота | Схема | Крок на деталі 200 мм |
|---|---|---|
| DeepCAD, Drawing2CAD, Text2CAD | куб 2×2×2, 256 рівнів (+`NORM_FACTOR` 0.75) | **≈1.04 мм** |
| CAD-MLLM | куб [−1,1]³, 256 рівнів | ≈0.78 мм |
| CAD-Recode v1.5, cadrille | unit box, сітка −100…+100, крок 1 | **1.00 мм** |
| CAD-Recode v1 | −50…+50 | 2.00 мм |
| CAD-Coder | масштаб не є входом узагалі | **не визначено** |
| Ortho2CAD, CADSmith, BlenderLLM | немає (числа в тексті коду) | обмежене лише даними |

Машинобудівний допуск на посадку — соті міліметра. **Розрив — два-три порядки.** Жодна модель
цієї сім'ї не може відрізнити 108 від 100 не тому, що погано навчена, а тому, що **в її
представленні такої різниці не існує**.

---

## 3. Гілка «модель пише КОД» — найближча до нашої архітектури

Це головна лінія 2024–2026 і головний висновок огляду: перехід від токенів до Python-коду дав
одночасно і кращі метрики, і читабельність. Причина сформульована в CAD-Recode §1: замість
вчити мережу *і* геометрії, *і* синтаксису власної мови, беруть LLM, що вже бачила Python.

### 3.1 CAD-Recode (ICCV 2025) — SOTA у reverse engineering

- **Архітектура:** Qwen2-1.5B + один лінійний шар + Fourier-енкодер точок. Вхід — **256 точок**.
- **Дані:** процедурно згенерований **1 000 000** послідовностей; тільки {Circle, RotatedRectangle}
  × {Union, Cut}, площини — три канонічні зі зсувом.
- **Точність** (CD ×10³ / IoU % / IR %): DeepCAD **0.30 / 92.0 / 0.4**; Fusion360 0.35 / 87.8 / 0.5;
  реальні скани CC3D **0.76 / 74.2 / 0.3**.
- **Ціна цієї валідності:** test-time sampling з **10 кандидатів** і вибором за Chamfer. Без нього
  IR = 4.9 % / 8.7 % / **16.8 %**. Тобто «99 % валідних» куплено 10-кратним інференсом.
- **Залізо:** тренування 1×H100 12 год; інференс 1.8 с/модель на H100. Dockerfile — CUDA 12.4,
  `flash-attn`, pytorch3d, headless-збірка Open3D з вихідників
  (<https://github.com/filaPro/cad-recode/blob/main/Dockerfile>). Формально код працює і на CPU
  («This notebook works on both CPU and GPU»), але задокументований шлях — CUDA-only.
- **Ліцензія: CC BY-NC 4.0 і на код, і на ваги, і на дані** [виміряно: `LICENSE.md` у репо,
  `license: cc-by-nc-4.0` у model card `filapro/cad-recode-v1.5`]. **У pip-пакет-продукт класти не можна.**
- **Обмеження, визнане авторами:** «CAD-Recode still lacks the expressiveness to model complex
  shapes that contain operations beyond the extrusion operation such as **revolution and fillet**».

### 3.2 cadrille (ICLR 2026) — мультимодальність + RL

Qwen2-VL-2B, вхід одночасно: хмара / 4 ортогональні рендери 268×268 / текст. Вихід — той самий
CadQuery. **Головне для нас — не модель, а результат RL-експерименту:**

| | DeepCAD | Fusion360 | CC3D |
|---|---|---|---|
| cadrille SFT | 0.18 / 87.1 / IR 2.1 | 0.19 / 79.8 / 2.8 | 0.54 / 61.8 / 5.9 |
| cadrille + RL (Dr. CPPO) | 0.17 / **90.2** / **IR 0.0** | 0.17 / 85.0 / 0.2 | 0.47 / 67.9 / **0.2** |

Нагорода суто програмна: `R = 10·IoU − 10·[invalid]`. Тобто **геометричний фідбек із ядра збиває
брак до нуля без 10× інференсу**. Це published-аргумент за verify-loop.

Друге корисне число (Table 8): «просто попросити фронтир-модель написати CadQuery» дає
**GPT-4o single-image IR 64.4 %**, GPT-o4-mini multi-view **IR 15.9 %**. Zero-shot цей шлях не працює.

Ліцензія: код Apache-2.0, **ваги CC BY-NC 4.0** [виміряно через HF API: `maksimko123/cadrille`].

### 3.3 Ortho2CAD (CMU, 07.2026) — єдина робота, де на вході є РОЗМІРИ

<https://arxiv.org/abs/2607.08891> · <https://github.com/AdityaJoglekar/Ortho2CAD>

- **Вхід:** растрові ортопроєкції у першому куті, зі штриховими невидимими лініями і
  **габаритними розмірами**. Генератор креслень зі STEP написано на pythonOCC і **опубліковано**.
- **Вихід:** CadQuery-код → STEP.
- **Цифри:** DeepCAD — Valid Codes **100 %**, IoU **0.7922** (CAD-Coder 100 % / 0.7361,
  GPT-5.2 90 % / 0.5987, Qwen3-VL-8B без файнтюну 70 % / 0.3747). Fusion360 після RL —
  **100 % / 0.5601** (GPT-5.2 78 % / 0.5181, CAD-Coder 90 % / 0.2473).
- **Оцінка на 100 прикладах з датасету** — вибірка мала, автори пояснюють вартістю.
- **Залізо:** SFT 11 год на 4×H100, RL 100 год на 4×H100; бекбон Qwen3-VL-8B.
- **[виміряно] LICENSE-файлу в репозиторії немає** → юридично «всі права збережені».
- Пряма цитата про масштаб: «Our RL reward of IoU is **scale invariant** and the dimensions units
  of the output models **can be scaled as per user requirement**» — тобто розміри на кресленні
  модель бачить, але метричність із них **не витягується**, вона лишається на користувачеві.

**Це найважливіша робота огляду для нас**: вона показує і стелю (IoU 0.56 на реальнішому
Fusion360 — тобто майже половина об'єму «не та»), і те, що напрям правильний.

### 3.4 CAD-Coder (MIT, 2505.14646) — і чому файнтюн обов'язковий

| Модель | Valid Syntax Rate | IoU_best |
|---|---|---|
| LLaVA-v1.5-13B (без файнтюну) | **0 %** | 0.0 |
| Qwen2.5-VL-72B | 94 % | 0.352 |
| GPT-4.5 | 84 % | 0.524 |
| **CAD-Coder** (LLaVA-1.5 + 163k) | **100 %** | **0.675** |

Базова 13B-модель **не знає CadQuery взагалі**. Висновок: або файнтюн, або RAG по документації API.
Код Apache-2.0; **ліцензія ваг на HF не вказана** [виміряно: `cardData` порожній] → юридично сіра зона.

Генералізація на реальні фото: 5 надрукованих деталей, «predicts the correct CAD operations,
but a **poor aspect ratio**» — тобто топологія переноситься, **пропорції — ні**.

### 3.5 Інші code-generating роботи (коротко)

| Робота | Суть | Головне число |
|---|---|---|
| **Text-to-CadQuery** 2505.06507 | +170k CadQuery-анотацій до Text2CAD | Qwen2.5-3B IR 6.5 %; Mistral-7B LoRA 1.32 %; fine-tuned GPT-4.1-mini **IR 0 %** |
| **CADCodeVerify** 2410.05340 | VLM сам собі ставить питання по рендеру і коригує код | compile rate 92 %→**96.5 %**; PC dist 0.153→0.127. Але «geometric solver» фідбек **кращий** (0.102) |
| **CADSmith** 2603.26512 | мультиагент + **RAG по документації API** + виміри з OCCT | exec rate 95 %→**100 %**, median IoU 0.8085→**0.9629** |
| **AIDL** 2502.09819 | solver-aided DSL: LLM пише *відношення*, солвер рахує координати | success 64 % vs OpenSCAD 79 %, але OpenSCAD-код «**highly difficult to edit**» |
| **BlenderLLM** 2412.14203 | Qwen2.5-Coder-7B + 12k bpy-скриптів | syntax error 31.4 %→**3.4 %**, обійшов o1-Preview. Але вихід — **меш** |
| **CAD-RL** 2508.10118 | RL post-training, «Precision Token Loss» саме під точність розмірів | датасет ExeCAD ~16.5k |

Спільний висновок: **12k–163k прикладів достатньо, щоб мала модель вивчила чужий CAD-API**
(BlenderLLM: 12k → падіння синтаксичних помилок у 9 разів). Це масштаб, реальний для диплому.

---

## 4. CAD-Assistant — LLM з FreeCAD як інструментом (найближче до нас взагалі)

<https://arxiv.org/abs/2412.13810> (ICCV 2025) · <https://github.com/dimitrismallis/CAD-Assistant>

Планер (GPT-4o) на кожному кроці генерує **Python-дію**, яка виконується інтерпретатором з
підключеним **FreeCAD Python API**; назад приходить стан документа (JSON + рендер). Моделі
бенчмарку попередньо завантажені у **`.FCStd`**. Це буквально наша архітектура, зроблена
дослідниками рік тому.

Інструменти (Table 1, дослівно): `Python`, **`FreeCAD`**, `Sketch Parameterizer` (скетч від руки →
CAD-скетч), `Sketch Recognizer`, `Solid Recognizer` (рендер + друк параметрів через PythonOCC),
`Constraint Checker`, `Crosssection Extract`.

| Задача | GPT-4o zero-shot | task-specific SOTA | CAD-Assistant |
|---|---|---|---|
| Autoconstraining, PF1 (700 скетчів SketchGraphs) | 0.693 | Vitruvion 0.706 | **0.979** |
| Autoconstraining, **CF1** | 0.274 | Vitruvion 0.238 | **0.484** (5-shot 0.529) |
| CAD-QA 2D / 3D (SGP-Bench) | 0.686 / 0.782 | — | **0.791 / 0.857** |
| Hand-drawn параметризація, Acc / CD | — | Davinci 0.789 / 1.184 | 0.784 / **0.680** |

Що з цього справді цінне:

1. **Абляція інструментів важливіша за промптинг**: без `Constraint Checker` PF1 падає
   0.979 → 0.747; без мультимодального recognizer → 0.726.
2. **Формат серіалізації стану в промпт міряний** (Table 2, GPT-4o, 2D SGP-Bench):
   JSON + point-based **0.748** > serialized graph 0.744 > HTML 0.710 > Markdown 0.706 >
   CSV 0.703 > **DXF 0.671**. Точний рендер 0.754 проти hand-drawn 0.616.
   → **давати стан як JSON з явними точками і додавати точний рендер.**
3. **Train-free розширення API**: нову FreeCAD-команду додають дописуванням docstring у
   `cad_assistant/prompts/freecad_doc.j2`. Файнтюн не потрібен, включно з Revolution/Fillet,
   яких немає в датасетах.
4. **Валідність використання інструментів 98.5 %** (ручна перевірка 200 траєкторій);
   помилки — «incorrect use of the FreeCAD API».
5. **CF1 = 0.484** — тобто навіть найкраща система світу ставить правильно **половину**
   constraint-ів. Автоконстрейнінг «на око» не працює; тому у нас constraint має слідувати
   з **прочитаного креслення**, а не з міркувань моделі.

**Чому не можна взяти as-is:** ліцензія **CC BY-NC 4.0** [виміряно: `LICENSE.md` у репо] —
несумісна з LGPL-продуктом; і потрібен `openai_api_key` у `config.json` → **не офлайн**.
Беремо ідеї й архітектуру, не код.

---

## 5. Гілка «токени послідовності» — і чому вона програла

DeepCAD → Text2CAD → Img2CAD → Drawing2CAD → CAD-MLLM. Тут вихід не код, а вектор команд.

Ключове порівняння, яке робить вирок усій гілці:

| Задача | Invalidity Ratio |
|---|---|
| DeepCAD, автоенкодинг (вхід = сама ж послідовність) | **2.72 %** |
| Drawing2CAD, 4 **векторні** види креслення | **20.31 %** |
| Drawing2CAD, 1 ізометричний вид | 21.40 % |
| Img2CAD (2410.03417), реальне зображення | **28.82 %** |
| Img2CAD, скетч/edge-вхід | **50.2 %** |

Тобто **вузьке місце — не представлення, а перехід «зображення → послідовність»**: одна з
п'яти-чотирьох спроб не дає навіть валідного тіла. Для «живого дерева фіч» це неприйнятно.

Дві поправки, що корисні нам:
- **Абляція Drawing2CAD підтверджує вектор проти растра** на однакових даних: 75.14 % / IR 23.40 %
  проти 70.49 % / 29.79 %. (Це вже цитувалось у README — джерело підтверджено.)
- **Text2CAD: F1 дуг лише 36.03 %** проти ліній 81.13 % і кіл 74.25 %. Криволінійні профілі —
  системна слабкість гілки.

**CAD-MLLM дає найважливішу методологічну поправку огляду.** Вони ввели топологічні метрики
(Segment Error, Dangling Edge Length, Self-Intersection Ratio, Flux Enclosure Error) і показали:

| Метод | Chamfer↓ | DangEL↓ | SegE↓ | SIR %↓ |
|---|---|---|---|---|
| NVDNet | **0.82** (найкраще) | **47.97** (найгірше) | 37.22 | 2.60 |
| CAD-MLLM | 1.85 | **0.46** | **1.66** | **1.31** |

**Chamfer/IoU не вимірюють придатність моделі як CAD-моделі.** Метод з найкращим Chamfer має
найгіршу топологію. Для нашої оцінки це означає: міряти `BRepCheck_Analyzer`, dangling edges,
self-intersections, і — головне — чи змінюється модель при зміні параметра.

---

## 6. Скетч і хмара точок → параметрика

| Робота | Що реально дає | Чому не для продакшну |
|---|---|---|
| **Free2CAD** (SIGGRAPH'22, MIT) | штрихи від руки → послідовність CAD-команд; є UI редагування кожної команди | на послідовностях ≥5 операцій «could only explain **half of the strokes**»; навчання 9+6 днів; **датасет недоступний** — «The current data hosting is broken» (README) |
| **Sketch2CAD** (SA'20, MIT) | інкрементально: один штрих → одна CAD-операція **в контексті** вже побудованої геометрії | лише **4 операції** (extrude, bevel, add/sub, sweep); потрібен GPU на інференсі |
| **Point2Cyl** (CVPR'22, MIT) | хмара → extrusion cylinders із замкненими диференційовними параметрами | скетч на виході — **хмара з 2048 точок, не криві**; нормалізація в одиничну сферу |
| **SECAD-Net** (CVPR'23, MIT) | self-supervised, дуже компактне дерево (#P = 4.30) | скетчі — **implicit поля** → B-spline контури без constraint-ів; **fine-tuning 3 хв на кожну форму**; бенчмарк усього 50+50 форм |
| **ComplexGen** (SIGGRAPH'22, MIT) | повний **B-rep chain complex**, topology inconsistency **0** | **немає історії побудови**; вилучення комплексу = ILP, **545 с на модель** |
| **UV-Net** (MIT) / **BRepNet** (**CC BY-NC-SA**) | енкодери B-rep: класифікація і **сегментація граней за операцією, що їх створила** (ExtrudeSide/Fillet/Chamfer/RevolveSide…) | не генерують нічого; але це єдиний спосіб «прочитати» імпортований STEP як фічі |
| **MiCADangelo** 2510.23429 | вперше — реконструкція зі скану **зі sketch-constraint-ами** | цифр в abstract немає |

Окремо: **Point2Primitive** (2505.02043) формулює вирок SDF-підходу прямо — implicit-скетчі дають
«unintended curved edges and models that are **difficult to edit**». Тобто гілку SECAD-Net брати не варто.

---

## 7. Датасети: де є мм і де є constraint-и

| Датасет | Обсяг | мм? | Constraint-и? | Ліцензія |
|---|---|---|---|---|
| **DeepCAD** | 178 238 | **ні** (нормалізовано) | ні | код MIT; дані — з Onshape, окремої ліцензії немає |
| **ABC** | ~1 000 000 B-rep | у STEP (не обговорено) | ні — «no sufficient information to recover how the designs are constructed» | **MIT** (пайплайн — GPL) |
| **Fusion 360 Gallery** | Recon 8 625; Segm 35 680; Assembly 8 251 | **ТАК: «All units are provided in cm»** (docs/reconstruction.md) | **ТАК** — `constraints` + `dimensions` у JSON | ⚠️ **non-commercial research only**; «If you are employed by a for-profit, commercial entity, your employer shall also be bound» [виміряно: LICENSE.md] |
| **SketchGraphs** | **15 000 000** скетчів | **ТАК, у параметрах**: 5 mm 3.51 %, 1 cm 3.35 %, 3 mm 2.76 %, 0.5 in 2.41 % | **ТАК — це і є граф constraint-ів** | код **MIT**; дані — «The original creators hold the copyright», Onshape ToU |
| **GenCAD-Code** | 163 000 «зображення↔CadQuery» | ні [виміряно: 81.8 % чисел = k/256] | ні | ліцензія не вказана |
| **Zero-to-CAD** (Autodesk, 2604.24479) | ~1 000 000 послідовностей, курований сабсет 100 000 | не заявлено | ні | **ваги Apache-2.0** [виміряно: `ADSKAILab/Zero-To-CAD-Qwen3-VL-2B`] |
| **HistCAD** (2602.19171) | **170 236** послідовностей із **явними constraint-ами** + STEP + рендери + текст | native CAD | **ТАК** + 3D-точкові референси для fillet/chamfer | не перевірено |
| **FllumaOne-100K** (2606.17696) | 100 000: програма + **структуроване дерево фіч** + STEP + хмара + 8 рендерів | так (OCCT) | дерево фіч | не перевірено |

**Найважливіший рядок таблиці — SketchGraphs.** Це єдине велике джерело **design intent** з
реальними одиницями, і воно MIT (код). Розподіл типів (Табл. 1 паперу):
Coincident 42.17 %, Projected 9.71 %, Distance 6.72 %, Horizontal 6.45 %, Mirror 5.54 %,
Vertical 4.78 %, Parallel 4.37 %, Length 3.68 %, Perpendicular 3.24 %, Tangent 2.94 %.
Примітиви: Line 68.47 %, Circle 9.97 %, Arc 9.45 %, Point 8.58 %, Spline 2.57 %.

Мапінг на `Sketcher::Constraint` FreeCAD (первинне джерело — `src/Mod/Sketcher/App/Constraint.h`,
enum `ConstraintType`): Coincident, Horizontal, Vertical, Parallel, Perpendicular, Tangent, Equal,
Distance/DistanceX/DistanceY, Radius, Diameter, Angle, Symmetric — **прямі**. Немає прямого
аналога: `Offset`, патерни, `Midpoint` (через `Symmetric`). `Projected` (9.71 %) — це не
constraint, а зовнішня геометрія. **~85 % за частотою мапляться один-в-один.**

Другий висновок з того ж паперу: autoconstrain baseline дає **F1 0.71** на 2.2 млн скетчів
навчання. Наш детермінований autoconstrain за допусками (горизонталь/вертикаль/дотик/збіг)
цілком може бути співмірним — **без ваг і без GPU**.

---

## 8. Свіже (2025–2026): що змінилось і що варте уваги

Перевірено існування і назви через arXiv [виміряно], ліцензії — через HF API [виміряно].

| Робота | Чому важлива | Ліцензія |
|---|---|---|
| **Zero-to-CAD** 2604.24479 (Autodesk) | ~1 млн виконуваних послідовностей, синтезованих **LLM-агентом у CAD-середовищі з фідбеком**; «rich vocabulary of operations **beyond sketch-and-extrude**»; файнтюн VLM обганяє GPT-5.2 | **ваги apache-2.0**, Qwen3-VL-2B ✅ |
| **HistCAD** 2602.19171 | Представлення + 170 236 послідовностей **з явними constraint-ами**; метрики **ER / cPCSR / OES** — міряють, чи виживає design intent **після зміни параметра** | не перевірено |
| **CADIR** 2608.00891 | Редагована IR поверх **OCCT/OCP**: construction graph з залежностями параметрів, constraint-ами і topology selections; **Geometric Signature Matching** для стабільних посилань на грані; адаптери відновлюють **нативну історію фіч у FreeCAD** | коду немає |
| **Pointer-CAD v2** 2606.29301 | Прямо атакує квантування: «Small numerical deviations… may still **violate industrial tolerance**»; Plan-Then-Construct — спершу план з **явним метричним масштабом**, параметри в словник, pointer на неперервні значення → **нуль помилки дискретизації** | коду немає |
| **Drawing-Recode** 2607.27558 | **Растрові** креслення (у т.ч. скановані) → CAD-код; окремий OCR-модуль + **Annotation Grounding Loss**, що явно зв'язує розмірну анотацію з геометрією | коду немає |
| **CADENA** 2608.00799 | Reverse engineering **по одній операції за раз** із порівнянням геометрії на кожному кроці | код + ваги + бенчмарк **MIT** ✅ |
| **CADEngBench** 2608.09296 | 300 параметричних деталей × 2 задачі + FEA у CalculiX. Головний висновок: «**editing supplied CAD is substantially easier than generating it**» | — |
| **BenchCAD** 2605.10865 | 17 900 CadQuery-програм, **106 промислових сімейств** (конічні шестерні, пружини, свердла). Висновок: моделі **замінюють sweep/loft на простий sketch-extrude** | — |
| **CADBench** 2605.10873 | 18 000 зразків, 1.4 млн згенерованих програм на 11 системах: «code-generating VLMs remain **far from reliable**» | **MIT** |
| **FllumaOne** 2606.17696 | 100k: програма + дерево фіч + STEP, усе валідовано ядром. Baseline Qwen2.5-Coder-1.5B LoRA: **99.98 %** валідного Python, **99.14 %** валідного STEP | не перевірено |

Чесно: **більшість abstract-ів 2026 пише «outperforms SOTA» без жодного числа**, і в багатьох
випадках коду немає («will be released»). Список hype-без-релізу: WDR, NURBGen, UniCAD, DreamCAD,
HoLa. Пряма генерація B-rep (HoLa, AutoBrep, DTGBrepGen) дає, де це виміряно, **82 % валідності**
— і в ній **немає ні історії, ні constraint-ів**, тобто це мертва геометрія за визначенням.

---

## 9. Комерція і продукти

| Продукт | Вихід | Метричні одиниці | Офлайн | Придатність |
|---|---|---|---|---|
| **Zoo (KittyCAD) Text-to-CAD** | **KCL-код** (справжня параметрика) + STEP + меші | **так, юніти першокласні**: `42mm`, тип `number(mm)`, mm/cm/m/in/ft/yd | **НІ** — «geometry engine run in the cloud»; `zoo-kcl` локально вміє лише parse/format/lint/mock_execute, `execute_code` вимагає `ZOO_API_TOKEN` | ідеї й тулінг — так; сервіс — ні. ⚠️ їхній ToS забороняє benchmarking і розробку конкуруючого продукту |
| **KittyCAD/ezpz** | автономний **2D solver обмежень** (Newton + аналіз DoF), 22 типи constraint-ів | — | **так, без хмари** | **MIT** ✅ — словник constraint-ів майже збігається з FreeCAD Sketcher |
| **modeling-app/public/kcl-samples** | 104 параметричні семпли з юнітами й інженерними розрахунками | так | так | **MIT** ✅ — готовий few-shot корпус «як виглядає параметричний намір» |
| **Backflip AI** | меш → feature tree + sketch constraints (Extrude/Revolve/Pattern/Chamfer/Fillet) | не задокументовано | ні (хмара) | найближче за метою, але docs порожні, API немає, метрик нуль |
| **Adam CAD / Leo AI** | агенти-оркестратори над чужою CAD/PLM | — | ні | геометрію з нуля не роблять |
| **Spline AI / Sloyd** | **меш** (Sloyd міряє в трикутниках) | ні | ні | не CAD |
| **Autodesk Project Bernini** | не оприлюднено | — | — | єдиний публічний артефакт орг. `autodesk` на HF — датасет `neuralCAD-Edit` під **CC BY-NC-4.0** |
| **Onshape AI Advisor** | чат по документації | — | ні | не генератор |
| **CADmium** | справжнє JSON feature tree, працює офлайн | так | так | ⚠️ ліцензія **Elastic 2.0** (не OSI, несумісна з LGPL), репо оголошено inactive |

**Емпірична перевірка, що стосується всіх без винятку** (виконана дочірнім агентом на
FreeCAD 1.1.3, `freecadcmd`): `Part::Box` 200×50×10 → експорт STEP → реімпорт дає `Part::Feature`
з єдиним редагованим параметром `Placement`; `Length`/`Width`/`Height` **зникають повністю**.
Тобто **вихід будь-якого вендора через STEP — мертва геометрія**, незалежно від того, наскільки
параметричним він був усередині.

---

## 10. Чи можна це переюзати, щоб зробити параметричну модель

Пряма відповідь на питання користувача, по пунктах.

**Ваги — ні.** Найточніші (CAD-Recode, cadrille, Text2CAD, CADFit) — **CC BY-NC**. Ті, що
Apache/MIT (Zero-to-CAD 2B, CADReasoner, CADEvolve, CADENA, CADFS-2B), вийшли в 2026 і **всі
навчені на нормалізованих даних без мм**. Жодні ваги не дадуть нам числа з креслення.

**Вихід — ні.** Ніхто не генерує іменованих параметрів. Це не недогляд: у **самих
тренувальних даних їх немає** [виміряно: 0 присвоєнь у 100 скриптах GenCAD-Code]. Constraint-и
генерує тільки CAD-Assistant, з CF1 0.484.

**Що переюзати можна — чотири речі:**

1. **Формат виходу: виконуваний код, а не токени.** Це published-висновок: перехід від
   DeepCAD-токенів до CadQuery дав IoU 46.7 → 92.0 при IR 0.4 %, а Ortho2CAD/CAD-Coder дають
   **100 % синтаксично валідного коду**. Для нас цільова мова — **FreeCAD Python API**
   (`Sketcher.addConstraint`, `PartDesign::Pad/Pocket/Revolution`), а не CadQuery: CadQuery
   виконується у **власному OCCT (OCP)**, паралельному до FreeCAD, і його результат потрапляє
   в документ лише як мертвий `Part::Feature`.
2. **Verify-loop із ядра, не з рендера.** cadrille: RL на `10·IoU − 10·[invalid]` збив IR
   5.9 % → 0.2 %. CADCodeVerify: «geometric solver» фідбек стабільно кращий за візуальний
   (PC dist 0.102 проти 0.132). CADSmith: виміри bbox/об'єму/solid-validity з OCCT → exec rate
   **100 %**. CAD-Assistant: без `Constraint Checker` PF1 падає 0.979 → 0.747.
   Наш аналог уже названий у README, фаза 4: DoF ≠ 0, `ConflictingConstraints`,
   `RedundantConstraints` — це дефект читання з конкретним індексом.
3. **Словник і статистика constraint-ів зі SketchGraphs** (MIT-код): 10 типів покривають ~85 %
   реального вжитку і мапляться на `Sketcher::ConstraintType` прямо. Плюс **ezpz** (MIT) як
   готовий приклад автономного 2D-солвера з 22 типами і аналізом DoF.
4. **Спосіб серіалізації стану в промпт** (CAD-Assistant, Table 2): JSON з явними точками
   **0.748** проти DXF 0.671 і CSV 0.703; точний рендер 0.754 проти hand-drawn 0.616. Це
   безпосередньо лягає на фазу 3 нашої архітектури.

**Наша структурна перевага, яку варто назвати в дипломі явно:** уся ця література бореться за
те, щоб **вгадати** розміри з форми, і впирається в квантування ≈1 мм. Ми **не вгадуємо**:
числа лежать у текстовому шарі PDF з координатами, а прольоти виносних ліній дають незалежну
арифметичну перевірку (`README.md` §3). Ми починаємо з того, що для них — недосяжна стеля.

---

## 11. Що з цього НЕ треба робити

- **Не брати CAD-Recode / cadrille / Text2CAD / CADFit ні в якому вигляді в продукт** — усі
  ваги **CC BY-NC 4.0**. Для порівняння в дипломі — можна; у pip-пакет — ні.
- **Не брати датасет Fusion 360 Gallery і код BRepNet** — non-commercial, причому ліцензія
  Fusion 360 явно зв'язує ще й роботодавця.
- **Не будувати нічого на DeepCAD-токенах.** Крок ≈1 мм на 200-мм деталі, IR 20–30 % на
  зображеннях, ні fillet, ні chamfer, ні revolve, ні pattern, F1 дуг 36 %.
- **Не генерувати CadQuery/build123d як цільову мову.** Так, `cadquery` 2.8.0 і `build123d`
  0.11.1 — Apache-2.0, і `cadquery-ocp` має колеса `macosx_11_0_arm64` / `win_amd64`
  [виміряно через PyPI JSON API], тобто офлайн-виконання реальне. Але це **окреме ядро OCCT**:
  результат заїде у FreeCAD як мертве тіло. Наша мова — FreeCAD Python API.
- **Не міряти якість Chamfer-ом чи IoU.** CAD-MLLM показав: метод з найкращим Chamfer (NVDNet
  0.82) має найгіршу топологію (DangEL 47.97). Міряти: валідність B-rep, dangling edges,
  self-intersections, і **чи змінюється модель при зміні параметра** (метрики ER/cPCSR/OES
  з HistCAD — правильний шаблон).
- **Не сподіватись, що модель сама поставить constraint-и.** Найкращий опублікований результат —
  CF1 **0.484** у CAD-Assistant, і той на GPT-4o з окремим `Constraint Checker`.
- **Не брати implicit/SDF-гілку** (SECAD-Net і похідні): Point2Primitive прямо показав, що вона
  дає «unintended curved edges and models that are difficult to edit», плюс 3 хв fine-tune на
  кожну форму.
- **Не брати ComplexGen** попри topology inconsistency = 0: **545 с ILP на модель** і немає
  історії побудови.
- **Не покладатись на хмарні продукти** (Zoo, Backflip, Adam, Leo): офлайн-режиму немає в
  жодного, а Zoo ще й забороняє ToS-ом benchmarking і конкуруючу розробку.
- **Не використовувати репозиторії без LICENSE-файлу** (Ortho2CAD, SOV-CAD, Pro-CAD, DTGBrepGen,
  окремий `KittyCAD/kcl-samples`) — юридично це «всі права збережені». Ідеї читати можна, код
  копіювати — ні.
- **Не вчити власну велику модель.** BlenderLLM показав, що 12k прикладів + Qwen2.5-Coder-7B
  (Apache-2.0) дає падіння синтаксичних помилок 31.4 % → 3.4 % — але це 4×A800. Для нас
  дешевший і чесніший шлях — **train-free RAG по docstring-ах FreeCAD API**, як у CAD-Assistant
  (98.5 % валідних викликів, нуль навчання) і CADSmith (exec rate 100 %).

---

## 12. Що лишилось неперевіреним

- Ліцензії HistCAD, FllumaOne, CADIR — не читав LICENSE напряму.
- Cумісність Apache-2.0 з LGPL-2.1 в один бік (Apache-2.0 → LGPLv3 однозначна; LGPL-2.1 —
  питання до юриста, не до первинного джерела).
- Autodesk Fusion «Automated modeling» / AutoConstrain — сторінки help.autodesk.com віддають
  403/404; Project Bernini — 404. Твердження про них у публічних оглядах не підтверджені.
- Backflip AI, Adam CAD — API-документації немає у відкритому доступі, метрик не публікують.
- Реальна швидкість 2B-моделі (Zero-to-CAD, CADENA) на CPU/Metal без CUDA — ніхто не міряв,
  і це критичне для нас число.
