# Генеративний image→3D: ландшафт і чи можна з нього зробити параметричну модель

*Огляд первинних джерел: arXiv (abstract + метод + Limitations), GitHub README/код/LICENSE,
HuggingFace file listing, офіційні docs/ToS. Кожне твердження несе URL джерела, що ним володіє.
Де числа немає у першоджерелі — написано **не виміряно**. Складено для FreeCAD Copilot як
поглиблення вироку, вже записаного в `README.md` §4 і `drawing-understanding-literature.md`.*

## Питання

Користувач питає: «чи є AI, який по зображенню будує 3D-об'єкт, які в нього підходи, і чи
зможемо ми щось з цього переюзати, щоб на його основі зробити ПАРАМЕТРИЧНУ модель».

Відповідь: **AI такий є, їх десятки, вони працюють, і жоден із них не будує параметричну
модель — за конструкцією, а не через незрілість.** Нижче — чому саме, з цифрами.

## Головне

- **Метричних міліметрів немає в жодної моделі жодного кластера.** Це не пропуск у реалізації,
  це нормалізація у препроцесингу. Дослівно: LRM — *"we normalize the shape to the box
  [−1,1]³ in world space"* ([2311.04400](https://arxiv.org/abs/2311.04400) §4.1); TripoSR —
  `renderer.radius: 0.87` = куб [−0.5,0.5]³
  ([config.yaml](https://huggingface.co/stabilityai/TripoSR/blob/main/config.yaml));
  TRELLIS — `np.clip(vertices, -0.5+1e-6, 0.5-1e-6)`, `voxel_size=1/64`
  ([dataset_toolkits/voxelize.py](https://github.com/microsoft/TRELLIS)); PartCrafter —
  `normalize_mesh(mesh, scale=2.0)` → `scale = 2.0/max(extents)`
  ([src/utils/data_utils.py](https://github.com/wgsxm/PartCrafter)); MeshAnything → [−0.5,0.5].
  **Одне фото не містить масштабу фізично**; моделі не «втрачають» мм, їх там ніколи не було.
- **Вихід усіх — поле, з якого витягли трикутники.** Triplane-NeRF (TripoSR, LRM, Instant3D),
  SDF/occupancy-латент (Hunyuan3D, PartCrafter, HoloPart, TRELLIS), 3D-гаусіани (TRELLIS),
  квантовані токени вершин трикутників (MeshGPT/MeshAnything). Ізоповерхня береться Marching
  Cubes / DMTet / FlexiCubes. **B-rep, ескізу, отвору, фаски як сутності в мережі не існує.**
- **Виміряно, наскільки погано це саме на механіці — вперше і однозначно.** OmniMech
  ([2608.05539](https://arxiv.org/abs/2608.05539), 251 000 розмірених креслень з допусками,
  метрики в мм²/мм³) прогнав image→3D моделі по кресленнях: похибка об'єму **HY3D-2 109.4 %,
  SAM 3D 218.3 %, TRELLIS 317.9 %, InstantMesh 5515.5 %**, VIoU 0.05–0.15. Дослівний вирок
  паперу: *"Hunyuan3D-2, InstantMesh, SAM 3D, and TRELLIS performed poorly on dimensioned
  engineering drawings, often treating the sheet as a single image and producing coarse,
  textured, or shallowly extruded geometry rather than the specified part."*
- **Навіть фронтирні VLM на повністю розміреному кресленні промахуються по об'єму на 17–146 %.**
  OmniMech, Table 2: *"Surface-area error ranged from 7.09–27.26 %, while volume error ranged
  from 17.41–145.82 %"* (тільки комерційні LLM). Найкращий результат — Gemini-клас, ~22 % по
  об'єму ≈ **7 % лінійно**. Це рівно масштаб плутанини «108 проти 100», і це **при тому, що
  число надруковане на аркуші**. Пряме підтвердження ADR-0019-висновку з `README.md`.
- **Єдина методика, що зберігає мм, — не нейромережа.** CADFit
  ([2605.01171](https://arxiv.org/abs/2605.01171), MIT + Siemens) — IoU-driven оптимізація
  CadQuery-програми під вхідний меш **у власних координатах меша, без нормалізації і без
  квантизації**. IoU 0.960 на DeepCAD, **Invalid Ratio 0.000 на кожному зрізі**. Але ліцензія
  CC BY-NC 4.0, і 235–904 с на деталь.
- **Зчепити генеративний меш із CADFit пробували — і виміряли деградацію.** CADFit + Hunyuan3D 2.0:
  IoU падає **0.873 → 0.669 (ABC Easy), 0.790 → 0.545 (Med), 0.547 → 0.380 (Hard)**, тобто
  −23…−31 %. Дослівно: *"failures in upstream image-to-3D models can propagate to the recovered
  CAD program"*. Абляція вибору генератора там же: Hunyuan3D IoU 0.692 проти **TRELLIS 0.202**.
- **Ліцензійне поле — мінне, і код ≠ ваги.** Wonder3D: код MIT, **ваги AGPL-3.0**. OpenLRM: код
  Apache-2.0, **ваги CC BY-NC 4.0**. MeshAnything/V2: код S-Lab NC, **а HF-картка каже `license: mit`**
  — пряма суперечність. Уся сім'я Hunyuan3D (і BPT): *"THIS LICENSE AGREEMENT DOES NOT APPLY IN
  THE EUROPEAN UNION, UNITED KINGDOM AND SOUTH KOREA"*. Для воркбенча, що ставиться з pip
  по всьому світу, це не «обмеження», а заборона.
- **macOS-шлях є рівно у двох моделей і обидва з зірочкою.** SF3D — єдиний з реальним
  `["cuda","mps","cpu"]` і Metal-кернелами, але README сам каже *"support is experimental"*, треба
  OpenMP + `PYTORCH_ENABLE_MPS_FALLBACK=1` + локальна компіляція двох C++-розширень, а **ваги
  gated** (ламає офлайн-pip). Hunyuan3D shape-гілка йде на `--device mps`, але ліцензійно мертва.
  Решта — `xformers`, `flash-attn`, `tiny-cuda-nn`, `nvdiffrast`, `spconv`, `diffoctreerast`.

---

## 1. Осі порівняння, які насправді вирішують

Задача воркбенча — не «отримати 3D», а «отримати `Sketch` + `Pad` з числом 108 у полі
`Length`». Тому з десятка популярних осей (FID, CLIP-score, час) значення мають чотири:

| вісь | чому вирішує | стан по всьому ландшафту |
|---|---|---|
| метричний масштаб | без мм результат неможливо звірити з кресленням | **немає ніде** (крім CADFit, що успадковує масштаб входу) |
| вихідне представлення | меш ніколи не стає деревом фіч | поле → MC-трикутники скрізь |
| редаговані параметри | користувач має міняти 108 на 110 | **немає ніде** у генеративних; є в CAD-DSL-моделях, але квантовані |
| ліцензія + офлайн pip | LGPL-воркбенч, macOS/Windows, без CUDA | проходять одиниці |

---

## 2. Кластер A — feed-forward LRM і multi-view diffusion

| модель | підхід | вихід | топологія | нормалізація | ваги / VRAM | час | CPU/MPS |
|---|---|---|---|---|---|---|---|
| **TripoSR** ([2403.02151](https://arxiv.org/abs/2403.02151)) | LRM: DINO ViT-B/16 → triplane | triplane NeRF → MC | triangle soup, `--mc-resolution` **256** | куб [−0.5,0.5]³ | 1.68 ГБ / ~6 ГБ | **<0.5 с A100** | CPU так, **MPS ні** |
| **LRM** ([2311.04400](https://arxiv.org/abs/2311.04400)) | feed-forward 500M → triplane | triplane NeRF | triangle soup | *"box [−1,1]³"* | ваг немає (Adobe) | <5 с A100 | — |
| **Instant3D** ([2311.06214](https://arxiv.org/abs/2311.06214)) | SDXL→4 види → sparse-view LRM | triplane NeRF | triangle soup | успадкована | ваг немає | ~20 с A100 | — |
| **InstantMesh** ([2404.07191](https://arxiv.org/abs/2404.07191)) | Zero123++ (6 видів) → LRM + FlexiCubes | меш | triangle soup, `grid_res 128`, `grid_scale 2.1` | ≈[−1.05,1.05]³ | 1.52+1.73 ГБ / не виміряно | ~10 с (GPU не названо) | **ні**, `device='cuda'` хардкод |
| **Zero-1-to-3** ([2303.11328](https://arxiv.org/abs/2303.11328)) | viewpoint-conditioned 2D дифузія | **зображення**, 3D лише через SJC | — | unit cube в метриках | 15.5 ГБ / **~22 ГБ** | не виміряно | ні |
| **Wonder3D** ([2310.15008](https://arxiv.org/abs/2310.15008)) | cross-domain multiview diffusion (RGB+нормалі) → NeuS | SDF → меш | triangle soup | unit cube | ~2.6 ГБ | 2–3 хв | ні (tiny-cuda-nn) |
| **SF3D** ([2408.00653](https://arxiv.org/abs/2408.00653)) | LRM + delighting + PBR + UV-unwrap | меш + UV + PBR | **DMTet + remesh `none/triangle/quad`** | `radius 1.0`, bbox-норм. | 4.02 ГБ / ~6 ГБ | **0.5 с H100** | **так: cuda/mps/cpu** |

**Що з Limitations прямо стосується механіки** (це найцінніше в кластері):

- LRM §4.3.2, три вбиті цвяхи в одному абзаці: *"incorrect assumptions of the camera parameters
  can lead to distorted shape reconstruction"*; *"our model is deterministic and is likely
  producing averaged modes of the unseens"* (наскрізний отвір — це occluded region, він
  усереднюється, тобто **заростає**); *"we cannot faithfully reconstruct the view-dependent
  appearance of some real-world materials, e.g., **shiny metals**, glossy ceramics"*.
- InstantMesh: *"our transformer-based triplane decoder produces **64×64 triplanes**, whose
  resolution may be a bottleneck for high-definition 3D modeling"* + *"[FlexiCubes] is less
  effective on modeling **tiny and thin structures**"*. Фасці 0.5 мм на деталі 100 мм фізично
  немає де закодуватися.
- Wonder3D: *"only produces normals and color images from six views… challenging for our method
  to accurately reconstruct objects with **very thin structures and severe occlusions**"*.
- SF3D сам визнає базову ваду сімейства: *"MC can cause '**stair-stepping**' artifacts"*.
  Наслідок арифметичний: MC на сітці 256 дає на плоскій грані шум ~1/256 габариту = **±0.4 мм
  на деталі 100 мм**, а гостра кромка 90° округлюється радіусом ≥ вокселя.

**Жоден із семи паперів не має оцінки на механічних деталях** — бенчмарки GSO, OmniObject3D,
Objaverse (меблі, іграшки, побутові предмети). Точність на різьбі/фасці/отворі — **не виміряно**
ніде, крім OmniMech (§6).

**SF3D — єдиний, у кого є quad.** Remesher — Instant Field-Aligned Meshes, target vertex count
слайдером. Це справді корисно (quad-flow легше ретопологізувати), але це **геометрична
пост-обробка меша**, вона не відновлює аналітичну площину чи циліндр і тим більше не дає `Pad`.

---

## 3. Кластер B — латентні 3D і part-aware

| модель | підхід | вихід | нормалізація | ваги / VRAM | ліцензія коду / ваг |
|---|---|---|---|---|---|
| **TRELLIS** ([2412.01506](https://arxiv.org/abs/2412.01506)) | rectified-flow над SLAT: розріджений воксель 64³ + DINOv2 | RF / 3DGS / меш (3 декодери) | [−0.5,0.5]³ | ~3.3 ГБ / **≥16 ГБ** | MIT / MIT, але сабмодулі `diffoctreerast` (INRIA NC) і Flexicubes (NVIDIA) |
| **Hunyuan3D 2.0** ([2501.12202](https://arxiv.org/abs/2501.12202)) | flow-matching DiT над vecset/SDF | SDF-грід → MC | `box_v=1.01` ≈ [−1,1]³ | 4.93 ГБ / 6 ГБ (shape), 16 ГБ (з текстурою) | **Tencent Community** |
| **Hunyuan3D 2.1** ([2506.15442](https://arxiv.org/abs/2506.15442)) | те саме + PBR-Paint, відкритий train | SDF → меш + PBR | те саме | 7.37 ГБ / 10 ГБ shape, **29 ГБ** повністю | **Tencent Community** |
| **2mini / 2mv** | 0.6B / 1.1B multiview DiT | те саме | те саме | 3.82 / 4.93 ГБ | **Tencent Community** |
| **Hunyuan3D 3.0** | — | — | — | **відкритих ваг немає** (HF `tencent/Hunyuan3D-3.0` → 401) | — |
| **PartCrafter** ([2506.05573](https://arxiv.org/abs/2506.05573)) | compositional latent DiT (файн-тюн TripoSG), N наборів токенів | **N окремих мешів** | `scale=2.0/max_extent` → [−1,1]³ | ~3.97 ГБ / ≥8 ГБ | MIT / MIT |
| **HoloPart** ([2504.07943](https://arxiv.org/abs/2504.07943)) | amodal-доповнення сегментів дифузією | меш на частину | [−1,1], bounds ±1.05 | ~3.83 ГБ / не виміряно | MIT / MIT |
| **PartField** ([2504.11451](https://arxiv.org/abs/2504.11451)) | PVCNN+триплан → 448-D feature field | **мітки на гранях**, не геометрія | [−1,1]³ | 1.24 ГБ | **NVIDIA NC** |

**Part-aware ≠ feature tree — і це важливо не переплутати.** PartField кластеризує фічі на гранях
(дендрограма granularity), HoloPart доповнює приховану частину сегмента, PartCrafter одразу
генерує N мешів. Максимум, що виходить, — **плоский assembly глибини 1**: набір мешів без
операцій, без порядку побудови, без параметрів і без обмежень. Це не `Pocket` після `Pad`.
До того ж HoloPart сам зазначає: *"The outcome of HoloPart is influenced by the quality of input
surface masks. Unreasonable or low-quality masks may lead to incomplete results"*.

**Час інференсу в TRELLIS / Hunyuan3D / PartCrafter / HoloPart у README і паперах не наведено —
не виміряно.** Єдина цифра в кластері: PartField «<1 s» на feed-forward і «~10 s» з кластеризацією.

**Розділу Limitations у Hunyuan3D 2.0 і 2.1 немає взагалі** (grep «limitation» = 0 збігів).
TRELLIS хвалить *"flat faces and sharp edges"* лише якісно, Appendix E — про підсвітку, не
про геометрію. Механічної оцінки немає в жодного — **не виміряно**. Арифметичне заперечення
натомість є: MC на 64³ (TRELLIS) чи 384³ (Hunyuan3D) фізично не тримає гостру кромку.

### 3.1 Tencent Hunyuan Community License — дослівно

Джерело: <https://github.com/Tencent-Hunyuan/Hunyuan3D-2/blob/main/LICENSE> (ідентично в 2.1,
3D-Part і BPT).

> "THIS LICENSE AGREEMENT DOES NOT APPLY IN THE EUROPEAN UNION, UNITED KINGDOM AND SOUTH KOREA
> AND IS EXPRESSLY LIMITED TO THE TERRITORY, AS DEFINED BELOW."

> п. 1.l: "'Territory' shall mean the worldwide territory, **excluding the territory of the
> European Union, United Kingdom and South Korea**."

> п. 2: "We grant You, **for the Territory only**, a non-exclusive, non-transferable and
> royalty-free limited license…"

> п. 4: "…the monthly active users… is greater than **1 million monthly active users** in the
> preceding calendar month, You must request a license from Tencent…"

> п. 5.b: "You must not use the Tencent Hunyuan Works or any Output… **to improve any other AI model**."

Два уточнення, що часто переказують неправильно: поріг тут **1 млн MAU, а не 100M** (100M — це
Llama-подібні ліцензії), і §5.c забороняє навіть *використання й показ Output* поза Territory.
Для дипломного проєкту, що ставиться з pip в Україні/ЄС, Hunyuan3D — **не «сірий», а прямо
виключений**.

### 3.2 Stability AI Community License (SF3D) — інший клас обмежень

Джерело: <https://github.com/Stability-AI/stable-fast-3d/blob/main/LICENSE.md>

> "…preserves free access to the Models for people or organizations generating annual revenue of
> less than US $1,000,000…"; "If at any time You… generate more than USD $1,000,000 in annual
> revenue… any licenses granted to You under this Agreement **shall terminate**…"

> "(iii) prominently display '**Powered by Stability AI**' on a related website, user interface,
> blogpost, about page, or product documentation."

> "…non-exclusive, worldwide, non-transferable, non-sublicensable, **revocable** and royalty-free
> limited license…"

Тут немає ані територіальних обмежень, ані MAU. Але є `revocable` і обов'язковий бейдж — а ваги
**gated** (треба HF-токен і схвалення), що ламає офлайн-встановлення з pip.

---

## 4. Кластер C — авторегресивна генерація мешів

| модель | стеля граней | квантизація | ваги / VRAM | час | ліцензія |
|---|---|---|---|---|---|
| **MeshGPT** ([2311.15475](https://arxiv.org/abs/2311.15475)) | ~768 (контекст 4608/6) | 128³ | — | — | Automotive Dev Public **NON-COMMERCIAL** v1.0 |
| **MeshAnything** ([2406.10163](https://arxiv.org/abs/2406.10163)) | **800** | 128³, [−0.5,0.5] | 2.38 ГБ / 7 ГБ | 30 с A6000 | **S-Lab 1.0 NC** |
| **MeshAnything V2** ([2408.02555](https://arxiv.org/abs/2408.02555)) | **1600** | [−1,1] (×0.9995) | 2.00 ГБ / 8 ГБ | 45 с A6000 | S-Lab NC, **а HF-картка каже `mit`** ⚠ |
| **BPT** | >8k | 7-біт, «normalize mesh to [−1,1]» | 1.64 ГБ / ~12 ГБ fp16 | ~2 хв | **Tencent Hunyuan 3D 2.0 Community** |
| **DeepMesh** | 30k @ 512³ | 512³ | 3.63 ГБ | **≥10 хв** | MIT |

**«Artist-like» не означає quad.** MeshGPT прямо в назві — *Triangle Meshes*; MeshAnything V2
описує AMT *"on triangle mesh"*, узагальнення на полігони лише декларує; BPT: *"each face
consists of 3 vertices, 7-bit quantization"*. Нативний quad є тільки в Rodin (`mesh_mode=Quad`);
у Tripo і Meshy quad — це auto-retopology поверх трикутників (Meshy чесно пише «quad-**dominant**»).

**Крок квантизації 128³ ≈ 0.8 % габариту ≈ 0.8 мм на деталі 100 мм.** Тобто навіть якби масштаб
був відомий, представлення саме по собі не розрізняє 108.0 і 108.8.

**CPU/MPS-шляху немає ні в кого:** MeshAnything V2 хардкодить `_attn_implementation="flash_attention_2"`
+ `to_bettertransformer()`, BPT — `.cuda().half()`, DeepMesh вимагає flash-attn + xformers +
sageattention + triton. Пошук по issues на `mps`/`macOS`/`cpu` у MAv2 і BPT — **0 результатів**.

Єдина реальна користь кластера для нас — **не готова деталь, а чистіший вхід**: 800–1600 граней,
вирівняних по кромках, реверсити плейн-фітом легше, ніж 200k MC-трикутників. Але ліцензійно
з п'яти проходить лише DeepMesh (MIT), який CUDA-only і 10 хв на меш.

---

## 5. Комерційні API

| сервіс | формати експорту | STEP / B-rep | ціна | права на вихід |
|---|---|---|---|---|
| **Tripo AI** | GLTF/USDZ/FBX/OBJ/STL/3MF | **немає** | 20–40 кредитів за image→3D, +5 за quad | ToS §5.2.1: Free-юзери **не володіють** виходом («Tripo retains all rights»); Paid володіють, але не можуть конкурувати |
| **Meshy** | glb/fbx/obj/usdz/blend/stl/3mf | **немає** | 20–35 кредитів (~$0.4–0.7 на Pro) | Paid володіє; **Free = CC BY 4.0** |
| **Rodin / Hyper3D** | glb/usdz/fbx/obj/stl, **нативний quad** | **немає** | 0.5–1.0 кредита ($1.5/кредит), API лише Business $96/міс | найм'якший ToS: *"we will not limit your use of such Output"* |
| **CSM.ai** | — | — | — | домен **не резолвиться** (NXDOMAIN на www/3d/docs) → первинних джерел немає, усе **не виміряно** |

Два незалежні вироки: **(1) жоден не експортує STEP/B-rep** — попри маркетинг «CAD-ready»
(«CAD-ready» у них означає «чистий меш з quad-топологією»); **(2) усі вимагають інтернету**, що
суперечить вимозі офлайн-роботи воркбенча. Плюс Tripo на безкоштовному тарифі не віддає навіть
прав на результат.

---

## 6. Що виміряно саме на механіці: OmniMech

[2608.05539](https://arxiv.org/abs/2608.05539) — перший бенчмарк, де метрики в **мм² і мм³**, а
не в безрозмірному Chamfer: 251 000 повністю розмірених і толерованих ортогональних креслень,
парних із native CAD, мешем, STEP і B-rep.

**Image→3D моделі, вхід — аркуш креслення** (Table 2; CD, SIoU, VIoU, F-score, похибка площі
мм² / %, похибка об'єму мм³ / %, час, pass rate):

| модель | CD | SIoU | VIoU | похибка площі | похибка об'єму | час |
|---|---|---|---|---|---|---|
| HY3D-2 | 18.4 | 0.10 | 0.07 | 144 070 мм² / **76.2 %** | 1 233 644 мм³ / **109.4 %** | 56.3 с |
| SAM 3D | 15.0 | 0.18 | 0.15 | 206 808 мм² / 130.1 % | 2 426 019 мм³ / **218.3 %** | 34.5 с |
| TRELLIS | 20.4 | 0.10 | 0.10 | 201 123 мм² / 115.4 % | 10 019 880 мм³ / **317.9 %** | 10.2 с |
| InstantMesh | 34.1 | 0.04 | 0.05 | 326 796 мм² / 200.5 % | 42 453 471 мм³ / **5515.5 %** | 110.9 с |
| CAD-Coder | 16.3 | 0.14 | 0.20 | 107 364 мм² / 171.8 % | 2 275 856 мм³ / **1395.1 %** | 126.8 с |

Pass rate у всіх image→3D = 100 %, бо «pass» означає лише «повернув валідний непорожній файл».
Тобто вони **завжди щось віддають, і це завжди не та деталь**.

**Фронтирні VLM, вхід — те саме розмірене креслення** (похибка об'єму, 2D-аркуш / аркуш+8 рендерів):
GPT-5.5 — 41.6 % / 25.8 %; Claude Opus 4.8 — 36.9 % / 31.8 %; Gemini 3.1 Pro — 23.7 % / 22.0 %;
GPT-5.4 mini — 126.7…145.8 %. Агреговано дослівно: *"Surface-area error ranged from 7.09–27.26 %,
while volume error ranged from 17.41–145.82 %"*.

Переклад у наші одиниці: 22–26 % по об'єму ≈ **7–8 % лінійно**. Це рівно та сама помилка, яку
`README.md` §1 називає галюцинацією прив'язки, — і вона лишається **при надрукованому числі
на аркуші**. Незалежне підтвердження головного архітектурного висновку: **у моделі не можна
питати число.**

Ще два корисні факти звідти: агентна петля з інструментами *"primarily improved reliability
rather than geometric accuracy"* (pass rate GPT-5.4 mini 55.5 % → 80.8 %, геометрія без змін),
а CAD-Coder має *"effective generation limit of approximately 4,096 tokens"*, через що складні
деталі просто обрізаються.

---

## 7. Міст «меш → параметри»: чи він існує

Це і є справжня відповідь на «чи можна щось переюзати».

| метод | вхід | вихід | мм? | цифри | ліцензія | pip/CPU |
|---|---|---|---|---|---|---|
| **CADFit** ([2605.01171](https://arxiv.org/abs/2605.01171)) | watertight меш | **CadQuery-програма** (extrude/revolve/fillet/chamfer/union/cut) | **так** — працює в координатах меша, без нормалізації і квантизації | IoU 0.960 DeepCAD / 0.917 Fusion360 / ABC 0.873–0.547; **IR = 0.000 скрізь** | **CC BY-NC 4.0** | так: `cadquery==2.6.1`, `cadquery-ocp`, `trimesh`, `pymeshlab`; torch опційно; 235–904 с/деталь |
| **Point2CAD** ([2312.04962](https://arxiv.org/abs/2312.04962)) | point cloud **вже сегментований** по surface id | поверхні + ребра + кути | успадковує вхід | SOTA на ABC | README: *"released under a CC-BY-NC 4.0 license"* (⚠ файл `LICENSE` у репо — Apache-2.0, суперечність) | **ні**: PyMesh, Docker, ParseNet/HPNet для сегментації |
| **Drawing-Recode** ([2607.27558](https://arxiv.org/abs/2607.27558)) | растрове креслення | SPCC / CadQuery | **частково так** | ACC_param **82.53 %**, MCD 7.86, IR 0.97 %, лише 0.85B параметрів | CC BY-NC-ND (папір) | коду/ваг **немає** |
| **Zero-to-CAD** ([2604.24479](https://arxiv.org/abs/2604.24479)) | multi-view рендери | **CadQuery з іменованими змінними** (`plate_thickness`, `fillet_radius`) | ні | IoU на реальному ABC падає **0.747 → 0.377** | CC BY 4.0 (папір/датасет), HF `ADSKAILab/zero-to-cad` | Qwen3-VL-2B |
| GenCAD / CADCrafter / GACO-CAD / SOV-CAD | фото або види | DeepCAD-команди | **ні** | — | GenCAD: **LICENSE-файлу немає взагалі**; решта коду не оприлюднила | — |

Ключові дослівні цитати про масштаб:

- SOV-CAD: *"normalizing and discretizing all continuous parameters into **256 quantized levels**"*.
- GenCAD: *"quantize their values into **256 levels** … 8-bit integers"*.
- CADCrafter: геометрія *"within a **unit bounding box** originating from the origin"*.
- GACO-CAD: тренування в `[-100,100]³`, рендер `[-1,1]³`, CD у `[-0.5,0.5]³`.
- Drawing-Recode — єдиний виняток серед навчених: *"we do **not** normalize the drawings … reflect
  the world coordinates of the model directly in the SVG, thereby fully preserving the dimensional
  information"* (+ OCR: YOLO26s-OBB 99.55 % F1, SVTR 98.54 %, Annotation Grounding Loss).

**Це підтверджує і поглиблює вирок з `drawing-understanding-literature.md`:** квантизація в 256
рівнів на одиничний куб ≈ 1 мм на деталі 200 мм, тобто модель **не може відрізнити 108 від 100
за побудовою**. Нове тут — що з'явилися рівно два способи це обійти, і обидва **відмовляються від
навченої регресії координат**: або підганяти програму геометрично (CADFit), або не нормалізувати
взагалі і читати розмір OCR-ом (Drawing-Recode). Другий — це саме те, що вже робить FCC.

**Найважливіший вимір усього документа** — CADFit + Hunyuan3D 2.0 end-to-end (єдиний
опублікований експеримент «фото → меш → параметрична програма»):

| зріз ABC | CADFit з GT-меша | CADFit з меша, згенерованого image→3D | втрата |
|---|---|---|---|
| Easy | 0.873 | **0.669** | −23 % |
| Medium | 0.790 | **0.545** | −31 % |
| Hard | 0.547 | **0.380** | −31 % |

Дослівно з Limitations: *"failures in upstream image-to-3D models can propagate to the recovered
CAD program"*. Абляція вибору генератора: Hunyuan3D IoU 0.692 проти **TRELLIS 0.202** на ABC Easy.

Тобто найкращий у світі опублікований ланцюг «картинка → параметрична модель» дає **IoU 0.38 на
складних деталях, без жодного міліметра**, за 4–15 хв на деталь, під CC BY-NC.

---

## 8. Не-генеративні альтернативи

| метод | масштаб | ліцензія | pip/офлайн/macOS | що вбиває на механіці |
|---|---|---|---|---|
| **COLMAP / pycolmap** | up-to-scale; мм лише через `model_aligner` з відомими координатами ≥3 камер (sim3) | **BSD-3** | **так**: колеса `macosx_14_0_arm64`, `win_amd64`, `manylinux` для cp310–cp314, 20–28 МБ | docs: *"Avoid **specularities on shiny surfaces**"*, *"Avoid completely **texture-less** images"* — це буквально опис фрезерованої алюмінієвої деталі. Dense-гілка: *"you can use all COLMAP functionality **except the dense reconstruction part**"* без CUDA |
| **Meshroom / AliceVision** | up-to-scale | **MPL-2.0** | ні: `ALICEVISION_USE_CUDA (default: ON)` — *"Enable build with CUDA (for feature extraction and depth map computation)"* | те саме |
| **Apple Object Capture** | **єдиний, у кого мм є чесно**: *"If your source images contain **depth data**, RealityKit uses it to calculate the **real-world size** of the scanned object. RealityKit can also create objects from images without depth data, but **you may have to scale the object**"* | пропрієтарна, у складі RealityKit | **macOS-only**, Swift, не pip. Вимога: *"a GPU with at least 4 GB of RAM and ray tracing support"*; iOS лише з LiDAR | LiDAR-точність на дрібній механіці — **не виміряно**; блиск і безтекстурність б'ють так само |
| **RealityCapture → RealityScan 2.0** | up-to-scale (мм через маркери/масштабні бари) | Epic EULA, безкоштовно під **$1M gross annual revenue** | **Windows-only** десктоп | те саме |
| **NeRF (nerfstudio)** | up-to-scale (масштаб від COLMAP) | **Apache-2.0** | частково | вихід — radiance field; меш через MC з тими ж сходинками |
| **instant-ngp** | up-to-scale | **NVIDIA NC**: *"The Work and any derivative works thereof only may be used or intended for use **non-commercially**"* | CUDA-only | — |
| **3D Gaussian Splatting** | up-to-scale | **Inria/MPII**: *"The Software may be used '**non-commercially**', i.e., for research and/or evaluation purposes only"* | CUDA-растеризатор | вихід — хмара анізотропних гаусіан, **не поверхня взагалі**; SuGaR успадковує ту саму NC-ліцензію |

Головне про фотограметрію в одному рядку: **вона чесніша за генеративні моделі (не вигадує
невидиме і може бути метричною), але вмирає рівно на тому класі поверхонь, з яких зроблені
механічні деталі** — блискучих і безтекстурних. І її метричність береться не з фото, а ззовні:
з LiDAR-глибини (Apple), з масштабної лінійки в кадрі або з відомих координат камер (COLMAP sim3).

---

## 9. Що ці моделі дають з ФОТО деталі, а що — з КАРТИНКИ виду з креслення

**З фото реальної деталі:** правдоподібний силует і топологію «схоже на кронштейн». Жодного мм.
Наскрізні отвори з високою ймовірністю заростають (LRM: occluded regions усереднюються). Блискучий
метал прямо названий провалом у LRM. Плоскі грані приходять хвилястими на ±0.4 % габариту,
кромки — округленими радіусом вокселя. Це **референс для розмови з користувачем**, не геометрія.

**З картинки виду з креслення:** нічого корисного, і це виміряно. OmniMech: моделі *"treating the
sheet as a single image and producing coarse, textured, or shallowly extruded geometry rather than
the specified part"*, VIoU 0.05–0.15, похибка об'єму 109–5515 %. Вони не розуміють, що три
прямокутники на аркуші — це три проєкції одного тіла; вони екструдують сам аркуш. **Ортографічний
вид — це не «зображення об'єкта» для цих моделей.**

Асиметрія варта того, щоб її вимовити вголос: наш вхід (креслення) для image→3D **гірший**, ніж
фото, хоча для людини він незрівнянно інформативніший. Інформація в кресленні лежить у
розмірних написах і виносних лініях, тобто саме в тому шарі, який ці моделі не читають взагалі.

---

## 10. Ліцензійна карта (код ≠ ваги)

| модель | код | ваги | вирок для LGPL-воркбенча з pip |
|---|---|---|---|
| TripoSR | MIT | **MIT** | ✅ єдиний повністю чистий кандидат |
| Zero-1-to-3 | MIT | MIT | ✅, але це не 3D-модель |
| PartCrafter, HoloPart | MIT | MIT | ✅ ліцензійно, ❌ CUDA |
| TRELLIS | MIT | MIT | ⚠ сабмодулі `diffoctreerast` (INRIA NC) + Flexicubes (NVIDIA) |
| InstantMesh | Apache-2.0 | Apache-2.0 | ⚠ `nvdiffrast` = NVIDIA SCL |
| OpenLRM | Apache-2.0 | **CC BY-NC 4.0** | ❌ комерція заборонена |
| Wonder3D | MIT | **AGPL-3.0** | ❌ отрута для LGPL-дистрибуції |
| SF3D | Stability Community | Stability Community, **gated** | ❌ revocable + бейдж + gated ваги ламають офлайн-pip |
| Hunyuan3D 2.0/2.1/2mini/2mv, BPT | Tencent Community | Tencent Community | ❌ **не діє в ЄС/UK/KR** |
| MeshGPT | Automotive Dev Public NC | — | ❌ |
| MeshAnything / V2 | S-Lab 1.0 NC | HF-картка каже `mit` ⚠ суперечність | ❌ (брати гіршу з двох) |
| PartField | NVIDIA NC | — | ❌ |
| CADFit, Point2CAD | CC BY-NC 4.0 | — | ❌ як залежність; ✅ як **описаний алгоритм** |
| DeepMesh | MIT | MIT | ✅ ліцензійно, ❌ CUDA-only, ≥10 хв |
| COLMAP / pycolmap | **BSD-3** | — | ✅ |
| nerfstudio | Apache-2.0 | — | ✅ |
| 3DGS, SuGaR, instant-ngp | NC | — | ❌ |

Практичне правило, яке варто зафіксувати: **HF-картка не є ліцензією**. У MeshAnything V2 і BPT
вона суперечить файлу `LICENSE` у репозиторії; у Point2CAD README суперечить власному `LICENSE`.
Брати завжди суворішу з двох і мати скріншот.

---

## 11. Що з цього НЕ треба робити

- **Не будувати шлях «фото/креслення → меш → параметри».** Це виміряно: найкращий опублікований
  ланцюг (Hunyuan3D → CADFit) дає IoU 0.38 на складних деталях і −31 % проти того ж CADFit з
  ідеального меша, за 4–15 хв. Ми не отримаємо кращого, ніж папір із Siemens.
- **Не тягнути Hunyuan3D ні в якому вигляді** — ліцензія прямо не діє в ЄС і забороняє навіть
  показ Output поза Territory. Це не «ризик», це відсутність гранту.
- **Не тягнути Wonder3D (ваги AGPL), OpenLRM (ваги CC BY-NC), PartField / instant-ngp / 3DGS /
  SuGaR (NC), MeshGPT / MeshAnything / BPT (NC або територіальні).**
- **Не покладатися на SF3D через ліцензію й gated-ваги**, навіть попри те, що технічно він
  найкращий у кластері (MPS, quad-remesh, DMTet проти stair-stepping).
- **Не вважати part-aware моделі деревом фіч.** PartCrafter/HoloPart/PartField дають плоский набір
  мешів або мітки на гранях — без порядку операцій, параметрів і обмежень.
- **Не вірити маркетингу «CAD-ready» у комерційних API** — жоден із Tripo/Meshy/Rodin не експортує
  STEP чи B-rep; «CAD-ready» означає «quad-меш». Плюс усі вимагають мережі, а Tripo на Free-тарифі
  не віддає прав на результат.
- **Не намагатися дістати мм з одного фото.** Це не інженерна, а інформаційна перешкода. Мм
  приходять ззовні: LiDAR-глибина (Apple), масштабна лінійка в кадрі, відомі координати камер
  (COLMAP `model_aligner`, ≥3 камери) — або, як у нас, **надруковане число на аркуші**.
- **Не використовувати фотограметрію на голій механіці без підготовки поверхні** — COLMAP сам
  просить уникати specular і texture-less, тобто описує саме фрезеровану деталь як анти-приклад.
- **Не запускати image→3D на аркуші креслення.** Виміряно: моделі екструдують сам аркуш
  (VIoU 0.05–0.15, похибка об'єму 109–5515 %).
- **Не ставити CUDA-залежності в продакшн-шлях воркбенча.** `xformers`, `flash-attn`,
  `tiny-cuda-nn`, `nvdiffrast`, `spconv`, `diffoctreerast`, `custom_rasterizer` не збираються на
  Apple Silicon без болю, а часто й зовсім.

## 12. Що (якщо дуже треба) можна переюзати — і в якій ролі

Три речі, і жодна з них не є моделлю з цього ландшафту:

1. **Алгоритм CADFit, реалізований з нуля.** Він повністю описаний у Appendix I–M, не потребує
   ваг, працює на CPU через `cadquery` + `cadquery-ocp` (pip, macOS/Windows) і **не нормалізує
   координати** — тобто зберігає мм. Ліцензія CC BY-NC поширюється на їхній код, не на ідею
   «IoU-driven line search по програмі з операцій extrude/revolve/fillet/chamfer». Для FCC це
   природний **верифікатор**, а не генератор: зібрати `Sketch`+`Pad` з прочитаних чисел і
   перевірити збіг з чимось. Це та сама роль, що вже прописана у Фазі 4 в `README.md`.
2. **TripoSR як proxy-меш для розмови, під явним ярликом «референс».** MIT/MIT, 1.68 ГБ,
   CPU-fallback, <0.5 с на A100. Ніколи на критичному шляху, ніколи як джерело числа.
3. **`pycolmap` (BSD-3, колеса під macOS arm64 і Windows), якщо колись знадобиться фото деталі**
   — і одразу з масштабною лінійкою в кадрі, бо інакше результат безрозмірний.

І один висновок, що не змінюється від жодної нової моделі: **параметр у дерево фіч потрапляє
тільки як прочитане число, а не як відновлена координата.** Увесь цей ландшафт відновлює
координати. Наше креслення числа **містить** — тому правильний хід не «додати image→3D», а
«не втратити те, що вже надруковано».

---

## Джерела

Кластер A: [TripoSR 2403.02151](https://arxiv.org/abs/2403.02151) ·
[LRM 2311.04400](https://arxiv.org/abs/2311.04400) ·
[Instant3D 2311.06214](https://arxiv.org/abs/2311.06214) ·
[InstantMesh 2404.07191](https://arxiv.org/abs/2404.07191) ·
[Zero-1-to-3 2303.11328](https://arxiv.org/abs/2303.11328) ·
[Wonder3D 2310.15008](https://arxiv.org/abs/2310.15008) ·
[SF3D 2408.00653](https://arxiv.org/abs/2408.00653) ·
[Stability Community License](https://github.com/Stability-AI/stable-fast-3d/blob/main/LICENSE.md)

Кластер B: [TRELLIS 2412.01506](https://arxiv.org/abs/2412.01506) ·
[Hunyuan3D 2.0 2501.12202](https://arxiv.org/abs/2501.12202) ·
[Hunyuan3D 2.1 2506.15442](https://arxiv.org/abs/2506.15442) ·
[Hunyuan3D 2.5 2506.16504](https://arxiv.org/abs/2506.16504) ·
[PartCrafter 2506.05573](https://arxiv.org/abs/2506.05573) ·
[HoloPart 2504.07943](https://arxiv.org/abs/2504.07943) ·
[PartField 2504.11451](https://arxiv.org/abs/2504.11451) ·
[Tencent Hunyuan Community License](https://github.com/Tencent-Hunyuan/Hunyuan3D-2/blob/main/LICENSE)

Кластер C: [MeshGPT 2311.15475](https://arxiv.org/abs/2311.15475) ·
[MeshAnything 2406.10163](https://arxiv.org/abs/2406.10163) ·
[MeshAnything V2 2408.02555](https://arxiv.org/abs/2408.02555) ·
[BPT License](https://github.com/Tencent-Hunyuan/bpt/blob/main/License)

Кластер D: [CADFit 2605.01171](https://arxiv.org/abs/2605.01171) ·
[OmniMech 2608.05539](https://arxiv.org/abs/2608.05539) ·
[CADCrafter 2504.04753](https://arxiv.org/abs/2504.04753) ·
[GACO-CAD 2510.17157](https://arxiv.org/abs/2510.17157) ·
[GenCAD 2409.16294](https://arxiv.org/abs/2409.16294) ·
[Zero-to-CAD 2604.24479](https://arxiv.org/abs/2604.24479) ·
[SOV-CAD 2607.04119](https://arxiv.org/abs/2607.04119) ·
[Drawing-Recode 2607.27558](https://arxiv.org/abs/2607.27558) ·
[Point2CAD 2312.04962](https://arxiv.org/abs/2312.04962)

Не-генеративні: [COLMAP tutorial](https://colmap.github.io/tutorial.html) ·
[COLMAP FAQ](https://colmap.github.io/faq.html) ·
[COLMAP BSD-3](https://raw.githubusercontent.com/colmap/colmap/main/COPYING.txt) ·
[pycolmap на PyPI](https://pypi.org/pypi/pycolmap/json) ·
[AliceVision INSTALL](https://github.com/alicevision/AliceVision/blob/develop/INSTALL.md) ·
[Apple: Capturing photographs for RealityKit Object Capture](https://developer.apple.com/documentation/realitykit/capturing-photographs-for-realitykit-object-capture) ·
[Apple: Creating 3D objects from photographs](https://developer.apple.com/documentation/realitykit/creating-3d-objects-from-photographs) ·
[RealityScan 2.0](https://www.realityscan.com/) ·
[3DGS License (Inria/MPII)](https://github.com/graphdeco-inria/gaussian-splatting/blob/main/LICENSE.md) ·
[instant-ngp License (NVIDIA)](https://github.com/NVlabs/instant-ngp/blob/master/LICENSE.txt) ·
[nerfstudio Apache-2.0](https://github.com/nerfstudio-project/nerfstudio/blob/main/LICENSE)
