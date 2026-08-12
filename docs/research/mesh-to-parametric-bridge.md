# Міст «меш → параметрична модель»: чи існує він взагалі

*Огляд літератури, продуктів і коду, тільки первинні джерела. Кожне твердження несе URL джерела,
що ним володіє. Власні заміри на цій машині (FreeCAD 1.1.3, macOS ARM) позначені **[виміряно]**
і відтворюються скриптами з розділу 9.*

## Питання

Попереднє дослідження (`README.md`, §4) відкинуло всі image→3D моделі одним рядком: «безрозмірний меш…
і меш ніколи не стає параметричним: `makeShapeFromMesh` дає по площині на трикутник». Цей файл перевіряє
саме цей вирок: **чи існує міст від меша/хмари точок до редагованого дерева фіч з розмірами в мм** —
класичний (RE-продукти, OCCT, FreeCAD), науковий (fitting примітивів), і зворотний (feature recognition,
inverse CSG). І скільки людської роботи лишається, якщо міст будувати.

## Executive summary

- **Автоматичного шляху «меш → дерево фіч Sketch+Pad у мм» не існує в жодному первинному джерелі.**
  Ланцюг рветься у чотирьох незалежних місцях: (1) масштаб, (2) поверхня→фіча, (3) ескіз→constraint,
  (4) порядок операцій. Кожен розрив підтверджено нижче цитатою автора або кодом.
- **Вирок README про `makeShapeFromMesh` підтверджено кодом і власним вимірюванням.**
  `TopoShape::setFaces` робить `BRepBuilderAPI_MakeFace` на **кожен трикутник** окремо
  ([TopoShape.cpp](https://raw.githubusercontent.com/FreeCAD/FreeCAD/main/src/Mod/Part/App/TopoShape.cpp)).
  **[виміряно]** циліндр Ø20×20 → меш 124 трикутники → shape зі **124 граней, усі `Plane`**, `Cylinder` — нуль.
  Після `refine`/`removeSplitter` лишається **34 площини**, циліндрична поверхня не з'являється ніколи.
- **Refine не «доліковує» це за побудовою.** `ModelRefine::FaceTypedPlane::isEqual` зливає лише площини
  з **паралельними нормалями** і відстанню < `Precision::Confusion` (≈1e-7)
  ([modelRefine.cpp](https://raw.githubusercontent.com/FreeCAD/FreeCAD/main/src/Mod/Part/App/modelRefine.cpp)).
  У трикутників циліндра нормалі різні → нуль об'єднань. Це не «треба підкрутити tolerance», це геометрія.
- **В OCCT прямого «трикутники → аналітичний циліндр» немає.** `ShapeCustom_Surface::ConvertToAnalytical`
  і `ShapeAnalysis_CanonicalRecognition` приймають **уже існуючу B-spline/Bezier поверхню**, не трикутники
  ([OCCT docs](https://dev.opencascade.org/doc/refman/html/class_shape_upgrade___unify_same_domain.html)).
  `BRepMesh` працює **тільки** в бік B-rep→меш.
- **Наука не дає міліметрів. Жодна робота.** Point2CAD ділить на `np.max(std)`
  ([utils.py](https://raw.githubusercontent.com/prs-eth/point2cad/main/point2cad/utils.py)), NerVE нормалізує
  в `[-1,1]³`, ParSeNet — unit cube, CAPRI-Net/D²CSG — `[-0.5, 0.5]`. Масштаб знищується на вході і
  **зворотне перетворення у вихідні файли не пишеться**.
- **Єдиний клас методів, що зберігає мм, — класична детекція примітивів.** CGAL Efficient RANSAC має
  `epsilon` як «absolute maximum tolerance Euclidean distance»
  ([CGAL docs](https://doc.cgal.org/latest/Shape_detection/index.html)) — тобто працює в одиницях входу.
  Але це **GPL-3.0-or-later OR commercial**
  ([SPDX у біндінгах](https://raw.githubusercontent.com/CGAL/cgal-swig-bindings/main/SWIG_CGAL/Shape_detection/impl.h)).
- **Feature recognition дає мітку на грань, а не фічу.** BRepNet: «eight labels for each face»
  ([2104.00706](https://arxiv.org/abs/2104.00706)). На **реальному** ABC UV-Net має per-class accuracy
  **56.81 %** і IoU **50.37 %** проти 99.95 % на синтетичному MFCAD
  ([2006.10211](https://arxiv.org/abs/2006.10211), Table 2).
- **Задача принципово недовизначена.** «A designer may choose to create a cylinder with an extruded circle
  or a revolved rectangle» ([2104.00706](https://arxiv.org/abs/2104.00706), §5.4). «Правильного» дерева фіч
  не існує — існує те, яке хотів автор. Обіцяти «відновимо оригінальне дерево» — обіцяти неможливе.
- **Sketch+extrude мережі формально є, практично непридатні для механіки.** Point2Cyl: похибка **осі
  витягування 8.137°** ([2112.09329](https://arxiv.org/abs/2112.09329), Table 1) і CD **13.889** на Fusion 360
  проти 2.052 у SECAD-Net ([2303.10613](https://arxiv.org/abs/2303.10613), Table 2). А CAD-експорт
  SECAD-Net — **stub function**, реально marching cubes → меш
  ([README](https://github.com/BunnySoCrazy/SECAD-Net)).
- **Комерційно дерево фіч будує рівно один продукт — SOLIDWORKS FeatureWorks**, і його вхід — **B-rep, не меш**,
  режим — best-effort з обов'язковим інтерактивним доопрацюванням
  ([help.solidworks.com](https://help.solidworks.com/2024/english/SolidWorks/fworks/c_Overview_of_FeatureWorks.htm)).
  Geomagic Design X дерево фіч не «відновлює» — його **будує людина** в 11-етапному воркфлоу, LiveTransfer
  лише транслює вже побудовану історію і «**will pause so that the features can be manually added**»
  ([support.geomagic.com](https://support.geomagic.com/s/article/Design-X-LiveTransfer-and-CAD-Compatibility)).
- **Під наші обмеження (pip, офлайн, macOS/Windows, LGPL-сумісно, без CUDA) не проходить жоден науковий репозиторій.**
  `uvnet`, `brepnet`, `occwl`, `secad`, `point2cad`, `complexgen` — **404 на PyPI**; `dgl` 2.2.1 має
  **тільки win_amd64** wheels; `pythonocc-core` має **0 файлів** у релізі; BRepNet — **CC BY-NC-SA 4.0**;
  CSGNet/D²CSG/InverseCSG — **без файлу LICENSE взагалі**.

---

## 1. Наскрізна таблиця: усе, що перевірено, за шістьма критеріями

| Метод / продукт | (а) мм | (б) вихід | (в) редаговані параметри | (г) ліцензія | (д) pip/офлайн/залізо | (е) точність на мех. деталях |
|---|---|---|---|---|---|---|
| **FreeCAD `Part.makeShapeFromMesh`** | так | compound плоских граней (1 трикутник = 1 грань) | **ні** | LGPL-2.1+ | вбудовано | **[виміряно]** vol-err −0.97 % (100 трик.) … −0.066 % (396 трик.) |
| **FreeCAD `Reen_ApproxCylinder`** | так | `Part::Cylinder` з `Radius`, `Height`, `Placement` | **так** — справжні параметри | LGPL-2.1+ | вбудовано, частина під `#if HAVE_PCL_*` | **[виміряно]** сегфолт з python-API (див. §9) |
| **FreeCAD `Mesh SegmentationBestFit`** | так | сегменти plane/cylinder/sphere + «can also identify their parameters» | так (параметри) | LGPL-2.1+ | вбудовано | не виміряно |
| OCCT Sewing / UnifySameDomain / ShapeFix | так | B-rep | ні | LGPL-2.1 + OCCT exception | `cadquery-ocp` (wheels) | н/д |
| Geomagic Design X | так | B-rep + **історія фіч** (побудована людиною) | так | комерційна, від $1 900/рік | **Windows-only**, NVIDIA GPU | не виміряно (вендор не публікує) |
| SOLIDWORKS FeatureWorks | так | **дерево фіч + ескізи + розміри** | **так** | комерційна (Premium/Pro) | Windows | не виміряно |
| SOLIDWORKS ScanTo3D | так | поверхні → knit → solid | ні | комерційна | Windows | не виміряно |
| Fusion 360 Convert Mesh | так | B-rep, **один вузол** timeline | ні | комерційна (Organic — платний extension) | ліміт ~**10 000** mesh-елементів | не виміряно |
| Point2CAD | **ні** (÷max std) | PLY + `topo.json` полілінії | ні (params лише в пам'яті) | конфлікт: LICENSE Apache-2.0 vs README CC-BY-NC | PyMesh не збирається на ARM | res-err 0.002→**0.011** при σ=0.05 |
| ParSeNet | **ні** (unit cube) | сегменти + примітиви + B-spline | частково | **LICENSE немає** | стек 2020 (tf-gpu 1.15) | seg iou 82.14, P-cov 92.97 |
| HPNet | **ні** | лише сегментація | **ні** (fitting не робить) | **LICENSE немає** | torch 1.7 + tf-gpu 1.15 | seg iou 94.15 |
| SED-Net | **ні** | сегменти → меш (`arg2mesh`) | ні | **LICENSE немає** | ваги на OneDrive/Baidu | не виміряно (ACM DL 403) |
| ComplexGen | **ні** | **B-rep chain complex** (JSON: patches+params+adjacency) | параметри патчів — так | MIT | **Gurobi+Mosek**, MinkowskiEngine/CUDA, фаза 3 Windows-only | P-cov 95.6 %→**69.9 %** при σ=0.05; ILP **545 с/модель** |
| NerVE | **ні** (`[-1,1]³`) | PWL-крива → сплайн | ні | MIT | CUDAExtension → macOS не збирається | CD 0.0012, HD 0.0714 |
| NEF | **ні** (принципово) | 3D-криві з мультив'ю | ні | **LICENSE немає** | NeRF-стек | синтетика |
| SplineGen | ні | B-spline | контрольні точки | GPL-файл, але README забороняє поширення | CUDA 11.8 | не виміряно |
| CGAL Efficient RANSAC | **ТАК** | plane/sphere/cylinder/cone/torus + інлаєри | так, але **рядком** `info()` | **GPL-3.0+ OR комерційна** | `pip install cgal`, є macosx_11_0_arm64 | E-RANSAC: res 0.022, recall 68.3 % |
| Open3D `segment_plane` | **ТАК** | лише площина (4×1) | так (a,b,c,d) | MIT | wheel `macosx_10_15_universal2` | не виміряно |
| pyransac3d | **ТАК** | plane/sphere/cylinder/cuboid/line/circle | так (numpy) | Apache-2.0 | pure-python | автори: циліндр «does NOT present good results on real data» |
| UV-Net / BRepNet / H-CADNet | вхід у мм, **вихід без розмірів** | **мітка класу на грань** | **ні** | MIT / **CC BY-NC-SA** / MIT | `dgl` тільки win_amd64 | ABC per-class **56.81 %**, IoU **50.37 %** |
| CSGNet / UCSG-Net / CAPRI-Net / D²CSG | **ні** (`[-0.5,0.5]`) | CSG-дерево квадрик/боксів → меш | ні | немає / MIT / MIT / **немає** | CUDA, PyMesh, 3–5 хв **на кожну деталь** | ABC CD 0.069 (D²CSG) … 0.576 (InverseCSG) |
| ExtrudeNet / SECAD-Net / Point2Cyl | **ні** | sketch+extrude (Безьє / implicit field) | формально так, **без constraint-ів** | MIT | CUDA; SECAD-Net CAD-експорт = **stub** | Fusion 360 CD 2.052 … **13.889** |

---

## 2. Класичний reverse engineering: що продукти реально автоматизують

### 2.1 Geomagic Design X — «параметричне» тут означає «людина малює ескізи»

Офіційний навчальний матеріал Hexagon розділяє два шляхи прямим текстом:

> «The two main approaches to creating a CAD object from scan data are **parametric modeling** and **surfacing**.
> Parametric modeling involves creating sketches and features … but the mesh is used as a **reference** for
> extracting geometry.»
> «**If sketches and features are created manually**, then the user can ignore holes in the mesh…»
> — https://support.geomagic.com/s/article/Autosurface?language=en_US

Тобто Auto Surface дає **NURBS-латки**, а не фічі з розмірами; параметричне дерево — це ручні ескізи.
LiveTransfer переносить «geometries, topologies, **modeling histories, and parameters**» у SW/NX/Creo/AutoCAD/
Inventor, а в Solid Edge/CATIA — «only selected solid or surface bodies … **and no feature tree**», і
«**If features cannot be transferred … the transfer will pause so that the features can be manually added**»
(https://support.geomagic.com/s/article/Design-X-LiveTransfer-and-CAD-Compatibility).

Обмеження, важливі для нас: **Windows 10+ only, NVIDIA GPU з OpenGL 4.0**, «Virtual environments or Dual Boot
are not officially supported» (System Requirements Guide v2026.1.0, PDF на dl.geomagic.com). Ціна від
**$1 900/рік** (Go), $4 190 (Plus), Pro за запитом; «Automatic Regions», «Auto-Sketch», «Exact Surfacing» —
**тільки план Pro** (https://hexagon.com/products/geomagic-design-x/geomagic-design-x-plans).
Офіційний beginner-воркфлоу — **11 етапів**. Годин «скан → параметричний солід» вендор **не публікує → не виміряно**.

### 2.2 Fusion 360 Mesh→BRep: «Parametric» ≠ ескізи з розмірами

Офіційна довідка (https://help.autodesk.com/cloudhelp/ENU/Fusion-Mesh/files/MESH-CONVERT-TO-SOLID.htm):

| Опція | Дослівно |
|---|---|
| Operation **Parametric** | «Creates a **feature** in the timeline that maintains its upstream parametric relationships» |
| Method **Faceted** | «Converts individual faces on the mesh body to individual faces on the new solid» |
| Method **Prismatic** | «**Merges groups** of faces in prismatic features into singular faces… Face groups are used to **infer** prismatic features» |
| Method **Organic** | потребує платного **Fusion Design Extension** |

Тобто «Parametric» = **один вузол `Convert Mesh`** у таймлайні, а не `Sketch → Extrude → Hole`.
Prismatic — це злиття граней, не розпізнавання фічі; ескізів воно не породжує.
Єдина офіційна кількісна межа: «the current limit for number of mesh elements is roughly **10,000**»
(Autodesk Technical Support, «How to insert a mesh body into Fusion»). Окремої команди «Mesh to BRep»
більше немає (https://help.autodesk.com/view/fusion360/ENU/?caas=caas/sfdcarticles/sfdcarticles/Mesh-to-BRep-menu-option-no-longer-available-in-Fusion-360.html).
Те, що Autodesk називає «Hole Recognition», живе в **CAM** і потребує платного Manufacturing Extension
(https://help.autodesk.com/view/fusion360/ENU/?guid=LP-READ-IRONHOLERECOGNITION) — циліндр розпізнається,
щоб його **насвердлити**, а не щоб створити фічу Hole.

### 2.3 SOLIDWORKS ScanTo3D — напівручний за офіційним описом

«ScanTo3D is available only in SOLIDWORKS Premium and SOLIDWORKS Professional»
(https://help.solidworks.com/2024/english/SolidWorks/scanto3d/c_Scanto3d_overview.htm).
Шлях 1 — «Direct Mesh Referencing»: **вручну малювати 2D/3D криві по мешу** → поверхні → trim & knit → solid.
Шлях 2 — Surface Wizard: Automatic «для органічних форм»; Guided «**requires more user interaction** and works
well for … mostly analytical shapes (simple shapes, such as machined parts), **but not for complex parts**».
Виходу «дерево фіч» немає в жодному з двох.

### 2.4 Ansys SpaceClaim — не перевірено

`help.spaceclaim.com` віддає **403 AccessDenied**, `ansyshelp.ansys.com` вимагає логін. Тверджень про
skin surface / facets→solid **не роблю** — первинного джерела немає. (Архітектурна властивість
«direct modeling без історії» тут не підтверджена.)

---

## 3. FreeCAD і OCCT: де саме обривається наш власний бік мосту

### 3.1 `Part.makeShapeFromMesh` — підтверджено кодом і виміряно

Python-обгортка ([TopoShapePyImp.cpp](https://raw.githubusercontent.com/FreeCAD/FreeCAD/main/src/Mod/Part/App/TopoShapePyImp.cpp)):
`tolerance = 1.0e-06` за замовчуванням, далі `setFaces(Points, Facets, tolerance)` + опційний `sewShape`.
Реалізація ([TopoShape.cpp](https://raw.githubusercontent.com/FreeCAD/FreeCAD/main/src/Mod/Part/App/TopoShape.cpp)):

```cpp
for (const auto& it : Topo) {                        // для КОЖНОГО трикутника
    newWire = BRepBuilderAPI_MakeWire(e1, e2, e3);
    newFace = BRepBuilderAPI_MakeFace(newWire);      // → окрема ПЛОСКА грань
    BuildTool.Add(aComp, newFace);
}
```
Коментар у самому коді: sewing вимкнений за замовчуванням, бо «the resulting shape isn't very usable in most
use cases… the computing time can be reduced by **90 %**».

Три висновки, які випливають із коду, а не з інтуїції:
1. 1 трикутник = 1 `Geom_Plane`. Апроксимації кривизни не відбувається **взагалі**.
2. `tolerance` — це **лише допуск зшивання** (`BRepBuilderAPI_Sewing::Init`), він не керує якістю геометрії.
3. Меш на 200 k трикутників → 200 k граней. Звідси і Fusion-ліміт 10 000, і те, що документ FreeCAD стає
   непридатним.

**[виміряно]** на FreeCAD 1.1.3 (macOS ARM), циліндр Ø20 × H20, `MeshPart.meshFromShape` → `makeShapeFromMesh`:

| LinearDeflection | трикутників | граней у shape | похибка об'єму | граней після `removeSplitter` | типи поверхонь |
|---|---|---|---|---|---|
| 1.0 | 100 | 100 | −0.970 % | 28 | лише `Plane` |
| 0.5 | 100 | 100 | −0.970 % | 28 | лише `Plane` |
| 0.1 | 124 | 124 | −0.641 % | 34 | лише `Plane` |
| 0.05 | 176 | 176 | −0.325 % | 47 | лише `Plane` |
| 0.01 | 396 | 396 | −0.066 % | 102 | лише `Plane` |

Оригінальна форма мала **3 грані** (`Cylinder`, `Plane`, `Plane`). Жодна кількість трикутників не повертає
`Cylinder`; вона лише зменшує похибку об'єму ціною кількості граней. Це і є визначення глухого кута:
**точність купується граньми, а не структурою**.

### 3.2 Чому «Refine shape» не рятує — підтверджено кодом

Вікі обіцяє: «It removes unnecessary edges from planar and cylindrical faces» (https://wiki.freecad.org/Part_RefineShape).
Реалізація ([modelRefine.cpp](https://raw.githubusercontent.com/FreeCAD/FreeCAD/main/src/Mod/Part/App/modelRefine.cpp)):

```cpp
bool FaceTypedPlane::isEqual(const TopoDS_Face& f1, const TopoDS_Face& f2) const {
    return plane1.Position().Direction().IsParallel(plane2.Position().Direction(), Precision::Confusion())
        && plane1.Distance(plane2.Position().Location()) < Precision::Confusion();
}
```
Зливаються **тільки грані одного типу поверхні на СПІВПАДАЮЧІЙ геометрії**. Трикутники, що апроксимують
циліндр, — це площини з **різними нормалями** → `isEqual` завжди `false`. **[виміряно]** підтверджує:
124 → 34 грані (злилися лише два торці, розбиті на трикутники), і всі 34 лишились `Plane`.

### 3.3 OCCT: canonical recognition приймає поверхню, а не трикутники

| Клас | Офіційне формулювання | Чого НЕ робить |
|---|---|---|
| `BRepBuilderAPI_Sewing` | «assemble contiguous shapes into one shape… Only manifold shapes will be found» | не змінює тип поверхонь |
| `ShapeUpgrade_UnifySameDomain` | «unify faces and edges … which lie on the **same geometry** … lying on **coincident** surfaces/curves» | не апроксимує різні площини циліндром |
| `ShapeFix_*` | «adapts shapes so as to make them as appropriate for use by OCCT as possible» | ремонт топології, не розпізнавання |
| `BRepMesh_IncrementalMesh` | «adds a **triangulation** of the shape» | працює **тільки** B-rep → меш |
| `ShapeCustom_Surface::ConvertToAnalytical` | «converting **BSpline and Bezier surfaces** to the analytical form» | вхід — поверхня, не трикутники |
| `ShapeAnalysis_CanonicalRecognition` | «explore the possibility of converting geometry into a **canonical form**»; вхід: edge/wire/face/shell | те саме |

Джерело: https://dev.opencascade.org/doc/refman/ і
https://dev.opencascade.org/doc/overview/html/occt_user_guides__shape_healing.html.

**Наслідок:** «підняти» фасетне сміття після `makeShapeFromMesh` жодним інструментом OCCT не можна. Єдиний
порядок дій, що працює: **спершу сегментувати меш і зафітити примітив, і лише потім будувати B-rep від
параметрів** — тобто нічого з фасетів не переносити.

### 3.4 Reverse Engineering WB: вікі бреше, але не в наш бік

Офіційна вікі: «**At the moment there isn't any functionality present in this workbench. It is used as a
sandbox by the programmers**» (https://wiki.freecad.org/Reverse_Engineering_Workbench).
Реальний [Command.cpp](https://raw.githubusercontent.com/FreeCAD/FreeCAD/main/src/Mod/ReverseEngineering/Gui/Command.cpp)
містить **12 команд**, з них три критично цікаві:

| Команда | Що робить за кодом |
|---|---|
| `Reen_ApproxPlane` | `MeshCore::PlaneFit` → створює `Part::Plane` з Length/Width/Placement |
| **`Reen_ApproxCylinder`** | `MeshCore::CylinderFit` → створює **`Part::Cylinder` з `Radius`, `Height`, `Placement`** |
| `Reen_ApproxSphere` | `SphereFit` → `Part::Sphere` з `Radius` |

Це **єдине місце у стоковому FreeCAD, де з меша дістаються справжні редаговані параметри в мм**. Плюс
`Mesh SegmentationBestFit`: «creates separate mesh segments for specified surface types… **The command can
also identify their parameters** which can be useful when re-engineering a mesh object»
(https://wiki.freecad.org/Mesh_SegmentationBestFit).

Але половина Python-API модуля `Reen` закрита `#if defined(HAVE_PCL_SURFACE / HAVE_PCL_SEGMENTATION /
HAVE_PCL_SAMPLE_CONSENSUS)`
([AppReverseEngineering.cpp](https://raw.githubusercontent.com/FreeCAD/FreeCAD/main/src/Mod/ReverseEngineering/App/AppReverseEngineering.cpp)) —
у білді без PCL цих функцій просто немає. І навіть там, де вони є, вони нестабільні:

**[виміряно]**, FreeCAD 1.1.3 macOS ARM, чиста хмара з 142 точок ідеального циліндра:

| виклик | результат |
|---|---|
| `ReverseEngineering.sampleConsensus(SacModel="Plane", Points=pc)` | ок: `(0,0,1,−20)`, `Probability 0.99` |
| `sampleConsensus(SacModel="Sphere", …)` | «успіх», але **сміття**: центр (0,0,10), R=14.142 (=√200, діагональ бокса) — валідації немає |
| `sampleConsensus(SacModel="Cylinder", …)` | **Segmentation fault 11** (exit 139) |
| `sampleConsensus(SacModel="Cone", …)` | **Segmentation fault 11** (exit 139) |

Тобто саме той примітив, що потрібен для отворів, у Python-API падає. (Причина ймовірно в тому, що
PCL `SACMODEL_CYLINDER` потребує нормалей, яких біндінг не передає — але це вже наша гіпотеза, не джерело.)
Робочий шлях — не Python-API `Reen`, а GUI-команда `Reen_ApproxCylinder`, яка викликає `MeshCore::CylinderFit`,
або власний фітинг.

**[виміряно]** проста альтернатива, що працює вже зараз і дає число в мм: переріз меша площиною
(`Mesh.crossSections`) + МНК-коло. На мешу циліндра Ø20 (LinearDeflection 0.05): 91 точка перерізу,
**фітований Ø = 19.9741 мм** при істинних 20, максимальна нев'язка 0.1226 мм. Тобто похибка **0.13 %** —
і це вже число, яке можна округлити до 20 і покласти у `Sketch`. Порівняй з розділом 4 `README.md`,
де такий самий прийом (глобальний fit кола) на 186 DPI-контурі креслення дав Ø34.87 при істинних 35.

---

## 4. Наука: сегментація + фітинг поверхонь (меш/хмара → B-rep)

### 4.1 Point2CAD (arXiv 2312.04962)

- **Вимагає готову сегментацію.** README: «requires the input point cloud in the `(x, y, z, s)` format»;
  у коді `assert points_labels.shape[1] == 4, "This pipeline expects annotated point clouds"`
  ([main.py](https://raw.githubusercontent.com/prs-eth/point2cad/main/point2cad/main.py)). Тобто він тягне
  за собою ParSeNet/HPNet зі стеком 2020 року.
- **Масштаб знищується**: `points = points / (np.max(std) + EPS)` після PCA-обертання
  ([utils.py](https://raw.githubusercontent.com/prs-eth/point2cad/main/point2cad/utils.py)); зворотне
  перетворення не зберігається.
- **Виходу B-rep немає**: на диск пишуться PLY-меші і `topo.json` з полілініями перетинів і кутами
  ([io_utils.py](https://raw.githubusercontent.com/prs-eth/point2cad/main/point2cad/io_utils.py)).
  Параметри примітивів (`pred_info["params"]`) **у файл не потрапляють** — живуть лише в пам'яті.
  STEP-експорту немає.
- Числа (ABC, нормалізовані одиниці): res-err **0.002** / P-cover **0.999** без шуму → **0.011 / 0.585**
  при σ=0.05 діаметра. Повна реконструкція: +GT-сегментація 0.011, +ParseNet 0.018 — тобто неідеальна
  сегментація коштує ~60 % приросту похибки. Limitations: «our method is **sensitive to the quality of the
  point cloud segmentation**».
- **Ліцензійна пастка**: файл `LICENSE` — Apache-2.0, README у тому ж коміті — «released under a
  **CC-BY-NC 4.0** license, which allows personal and research use only». Конфлікт у первинному джерелі.
- **Блокер встановлення**: PyMesh. `pymesh2` на PyPI має лише `cp35-macosx_10_11_x86_64` wheel → на ARM
  не ставиться. Dockerfile збирає PyMesh із джерел на базі `nvcr.io/nvidia/pytorch:21.05` + Python 3.7.

### 4.2 ParSeNet / HPNet / SED-Net

| Метод | seg iou | res | P-cover | Ліцензія | Ваги |
|---|---|---|---|---|---|
| RANSAC (baseline у ParSeNet) | 67.21 | 0.0220 | 83.40 | — | — |
| SPFN | 69.01 | 0.0212 | 88.40 | — | — |
| ParSeNet + e2e + opt | **82.14** | **0.0111** | **92.97** | **немає файлу** | Google Drive |
| HPNet (лише сегментація) | 94.15 | 0.008 | 94.02 | **немає файлу** | Google Drive |
| SED-Net | не виміряно (ACM DL 403) | — | — | **немає файлу** | OneDrive/Baidu |

Джерела: [2003.12181](https://arxiv.org/abs/2003.12181) Table 1; [2105.10620](https://arxiv.org/abs/2105.10620)
(увага: правильний arXiv-ID HPNet саме такий; 2107.02458 — це стаття про рівняння Больцмана).
Point2CAD прямо пише, що HPNet «does not fit actual primitives».
ParSeNet сам себе називає «editable and interpretable», але «editable» там означає CAD-операції над патчами,
**не мм-розміри**. Відсутність LICENSE-файлу = «всі права застережено» → юридично брати не можна.

### 4.3 ComplexGen (arXiv 2205.14573) — єдиний зі справжнім B-rep, і саме він найдорожчий

Вихід — chain complex у JSON: `patches[i].type` ∈ {Plane, Sphere, Cylinder, Cone, Torus, BSpline},
`patches[i].params` довжиною 7 (для циліндра — вісь + точка + **радіус**), `corners`, матриці суміжності
`patch2curve` / `patch2corner` / `curve2corner`
([опис формату](https://raw.githubusercontent.com/guohaoxiang/ComplexGen/main/docs/geometric_refinement_json_description.md)).
**Але криві зберігаються лише як 34 семпльовані точки, без параметрів** — тобто радіус кола-ребра (наш отвір!)
як число не існує. І все це в нормалізованому боксі.

Числа: recall **87.9 %**, P-cov **95.6 %**, patch-to-patch 0.191 (краще за ParseNet 0.376 і HPNet 0.343).
Під шумом σ=0.05: P-cov **95.6 → 69.9 %** (для порівняння ParseNet **93.0 → 44.8 %**).

Три незалежні дискваліфікатори з README/PDF:
1. «you need also **mannully setup Gurobi license**», `pip install gurobipy==9.1.2 && pip install Mosek` —
   комерційні солвери з ліцензійним сервером;
2. MinkowskiEngine v0.5.0 — «Ubuntu >= 14.04, **CUDA >= 10.1.243**»;
3. «Code of this phase **can be compiled only under Windows**» (фаза 3).
Плюс продуктивність: «chain complex extraction that solves integer linear programming, taking **545 seconds
on average**», README: «average extraction time for each model is **10 minutes**».

### 4.4 Ребра і криві: NerVE, NEF, SplineGen

- **NerVE** ([2303.16465](https://arxiv.org/abs/2303.16465), MIT): воксель-сітка 32³ → PWL-крива →
  «parametric curves can be obtained via **off-the-shelf spline fitting**». Тобто сплайн, не розмір.
  CD 0.0012 / HD 0.0714 проти PIE-NET 0.0074/0.1318. Вхід нормалізується у `[−1,1]³`.
  **Збірка вимагає `CUDAExtension`** ([setup.py](https://raw.githubusercontent.com/uhzoaix/NerVE/main/network/avg_pooling/setup.py)),
  `torch==1.12.1+cu113` → на macOS ARM не збирається.
- **NEF** ([2303.07653](https://arxiv.org/abs/2303.07653)) — вхід **каліброві мультив'ю-зображення**, не хмара;
  масштаб з зображень без еталона невідновний за визначенням (це той самий вирок, що в `README.md` §5).
  LICENSE у репо немає.
- **SplineGen** — GitHub показує GPL-3.0, README прямо суперечить: «**No secondary use, such as copy,
  distribution, diversion, business purpose, etc., is allowed**». Використовувати не можна.

### 4.5 Efficient RANSAC — єдина гілка, де мм виживають

Schnabel, Wahl, Klein, «Efficient RANSAC for Point-Cloud Shape Detection», CGF 26(2), 2007 — алгоритм,
на який посилається CGAL. У реалізації CGAL `epsilon` — «**absolute** maximum tolerance Euclidean distance
between a point and a shape», п'ять примітивів (plane, sphere, cylinder, cone, torus), потрібні нормалі
(https://doc.cgal.org/latest/Shape_detection/index.html). **Абсолютний epsilon означає, що масштаб входу
зберігається** — це і є ключова відмінність від усієї нейромережевої гілки.

| Реалізація | pip | macOS ARM wheel | Ліцензія | Обмеження |
|---|---|---|---|---|
| CGAL Shape_detection (`pip install cgal` 6.0.1) | так | **так**, `macosx_11_0_arm64` | **GPL-3.0-or-later OR комерційна** ([SPDX у impl.h](https://raw.githubusercontent.com/CGAL/cgal-swig-bindings/main/SWIG_CGAL/Shape_detection/impl.h)) | параметри повертаються **рядком** `info()`, типізованих геттерів немає |
| Open3D `segment_plane` | так (0.19.0) | так, `universal2` cp310–312 | MIT | **лише площина** |
| pyransac3d 0.6.0 | так | pure-python | Apache-2.0 | у коді циліндра: «!!! warning The cylinder RANSAC **does NOT present good results on real data**» |

Наслідок для ліцензії: CGAL Shape_detection зробить **весь воркбенч GPL-3.0**, а не LGPL. Це свідоме
рішення, яке треба фіксувати ADR-ом, або писати власний фітинг циліндра/сфери (Levenberg–Marquardt поверх
`scipy`) — див. §8.

---

## 5. Від B-rep назад до дерева фіч

### 5.1 AAG (Joshi & Chang, 1988) і його промислова межа

AAG — граф, де вершини = грані, ребра = ребра, атрибут = опукле/увігнуте; фіча = підграф-шаблон.
Спостереження, яке досі використовують мережі: «a face with all convex edges cannot be a part of a concave
feature» ([2104.00706](https://arxiv.org/abs/2104.00706)).

Чому ламається: коли дві фічі перетинаються, перетин «з'їдає» грані й ребра обох — шаблон більше не збігається.
Первинне формулювання: «Encoding all machining knowledge into a rule-based system is challenging… leading to
**poor adaptability, particularly in handling complex intersecting features**»
([2504.07378](https://arxiv.org/abs/2504.07378)). Hint-based обхід (Han & Requicha 1997) «remains challenging…
**challenging to achieve fully automated and high-precision** [recognition]» — там же.

**Скільки коштує промислова AAG-евристика — виміряно незалежно:** Autodesk Shape Manager, комерційне ядро з
rule-based FR, на Fusion 360 Gallery Segmentation дає **64.57 % per-face accuracy / 49.53 % IoU**, і
«**does not identify any modeling feature type for 13 % of the faces**»
([2104.00706](https://arxiv.org/abs/2104.00706), §5.4).

### 5.2 Навчений FR: мітка на грань ≠ фіча

**Це головна підміна понять у всій темі.** UV-Net формулює задачу як «the problem of **segmenting the faces**
of a B-rep»; BRepNet — «a set of **eight labels for each face**: ExtrudeSide, ExtrudeEnd, CutSide, CutEnd,
Fillet, Chamfer, RevolveSide, RevolveEnd». Ані ескізу, ані площини ескізу, ані довжини витягування, ані
порядку операцій, ані constraint-ів на виході **немає взагалі**.

| Модель | Датасет | per-face acc | per-class acc | IoU |
|---|---|---|---|---|
| UV-Net | MFCAD (синтетика, 15 488) | **99.95** | 99.93 | 99.87 |
| UV-Net | **ABC (реальний CAD)** | 88.87 | **56.81** | **50.37** |
| BRepNet (winged edge) | Fusion 360 Gallery (35 858) | 92.52 | — | 77.10 |
| ASM (комерційний) | Fusion 360 Gallery | 64.57 | — | 49.53 |
| Hierarchical CADNet | MFTRCAD (синтетика) | 78.39 | — | — |
| BRepFormer | MFTRCAD | 93.16 | — | — |

Джерела: [2006.10211](https://arxiv.org/abs/2006.10211) Table 2; [2104.00706](https://arxiv.org/abs/2104.00706)
Table 1; [2504.07378](https://arxiv.org/abs/2504.07378) Tables 3–4.

Дві застороги, які треба нести в диплом:
1. **Розрив синтетика/реальність — 43 пункти** (99.95 → 56.81 per-class). MFCAD — це куб із наліпленими фічами.
2. «92 % accuracy» BRepNet заробляється на дисбалансі: **>50 % граней у датасеті — один клас ExtrudeSide**,
   тоді як RevolveEnd — 0.08 %, CutEnd — 2.35 %, Chamfer — 2.94 %.

Інженерні блокери: `uvnet` і `brepnet` — **404 на PyPI**; `dgl` 2.2.1 має **тільки win_amd64** wheels;
`occwl` — 404 на PyPI; **BRepNet під CC BY-NC-SA 4.0** (NonCommercial) — юридичний глухий кут.

### 5.3 Inverse CSG / sketch-extrude: єдина гілка, що формально цілиться в наше завдання

| Метод | Що на виході | Sketch+Extrude? |
|---|---|---|
| CSGNet ([1712.08290](https://arxiv.org/abs/1712.08290)) | CSG-програма над фіксованим набором примітивів | ні |
| UCSG-Net ([2006.09102](https://arxiv.org/abs/2006.09102)) | «only boxes and spheres» (за оцінкою CAPRI-Net) | ні |
| CAPRI-Net ([2104.05652](https://arxiv.org/abs/2104.05652)) / D²CSG ([2301.11497](https://arxiv.org/abs/2301.11497)) | квадрики, зібрані intersect/union/difference | ні |
| **ExtrudeNet** ([2209.15632](https://arxiv.org/abs/2209.15632)) | ескіз із **4 кривих Безьє** + витягування | формально так |
| **SECAD-Net** ([2303.10613](https://arxiv.org/abs/2303.10613)) | ескіз як **неявне 2D-поле** + витягування | формально так |
| **Point2Cyl** ([2112.09329](https://arxiv.org/abs/2112.09329)) | «extrusion cylinders»: 2D-ескіз + вісь + діапазон, closed-form | формально так |

Метрики на **механічних** даних (Fusion 360, [2303.10613](https://arxiv.org/abs/2303.10613) Table 2):

| | UCSG-Net | CSG-Stump | **Point2Cyl** | ExtrudeNet | SECAD-Net |
|---|---|---|---|---|---|
| CD ↓ | 2.950 | 2.781 | **13.889** | 2.263 | **2.052** |
| ECD ↓ | 5.277 | 4.590 | 14.657 | 3.558 | 3.282 |
| NC ↑ | 0.770 | 0.744 | 0.669 | **0.819** | 0.803 |

Тобто метод, що прямо цілиться в «sketch + extrusion axis + range», геометрично **у ~6.8 раза гірший** за
implicit-конкурента. Власні числа Point2Cyl: **похибка осі витягування 8.137°** (Fusion Gallery) / 7.923°
(DeepCAD), seg IoU 0.736. Для деталі, де витягування має бути строго нормальним до площини, 8° — брак.

Чотири речі, яких немає **ніде** в цій гілці:
1. **Міліметрів.** Усе в `[−0.5, 0.5]`, «CD computed on 8k surface sampled points and **multiplied by 1,000**».
2. **Constraint-ів.** Слово в сенсі геометричних обмежень ескізу в цих паперах відсутнє: оптимізують CD/IoU,
   а не редагованість.
3. **Порядку операцій.** Point2Cyl, Limitations: «Our approach **does not make use of a known ordering** for
   the decomposition or reconstruction».
4. **Операцій, крім union витягувань.** SECAD-Net, Conclusion: «we plan to extend our approach to learn more
   CAD-related operations such as **revolve, bevel, and sweep**» — тобто зараз ні revolve, ні fillet, ні chamfer.

**Найважливіша знахідка блоку** — README SECAD-Net:
> «📢: The current code can only use **marching cubes to generate mesh**, and the code to generate CAD format
> is currently a **stub function**.»

Тобто найкращий за CD sketch-extrude-метод на практиці віддає **меш**, з якого ти починав; твердження в
abstract «can be directly fed into off-the-shelf CAD software for sketch-level editing» **кодом не підкріплене**.
D²CSG експортує в **OpenSCAD** (`quadric_to_basic.py`) — скриптовий CSG, не PartDesign з ескізами.

Вартість: CAPRI-Net вимагає **per-shape fine-tuning ≈3 хв GPU на кожну деталь**, D²CSG — ≈5 хв на RTX 2080 Ti.
Ліцензії: **CSGNet, D²CSG, InverseCSG — файлу LICENSE немає взагалі**; InverseCSG — тільки Linux/WSL.

### 5.4 Комерційно: FeatureWorks — єдиний, хто справді будує дерево

> «Recognized features are the same as features that you create using the SOLIDWORKS software. **You can edit
> the definition of recognized features to change their parameters.** For features that are based on sketches…
> **you can edit the sketches from the SOLIDWORKS FeatureManager design tree**.»
> — https://help.solidworks.com/2024/english/SolidWorks/fworks/c_Overview_of_FeatureWorks.htm

І навіть розміри: «FeatureWorks can automatically add **dimensions and relations**… supports **baseline, chain,
and ordinate** dimensioning schemes… recognizes **concentric and other relations**»
(c_Auto_Dims_Rels.htm). Це рівно те, що ми хочемо — і ось чого воно офіційно **не** гарантує:

| Обмеження (дослівно) | Сторінка |
|---|---|
| «**Partial imprints of features cannot be recognized.**» | c_Overview_of_FeatureWorks.htm |
| «FeatureWorks does not recognize **vertex fillets** / **vertex chamfers**.» | c_Fillets.htm / c_Chamfers.htm |
| «**Multiple hole recognition is not supported.**» | c_Holes.htm |
| «**Hole - Protrusion**: Other geometry is protruding into the hole» (перетинні фічі) | c_Holes.htm |
| «Fillet - Large Radius… Hole - Large Diameter: too large to recognize» | c_Fillets.htm / c_Holes.htm |
| «attempts to automatically recognize **as many features as possible**», далі Interactive, де **людина** обирає тип фічі та грані | c_Automatic_versus_Interactive_Feature_Recognition.htm |
| «you can decide if you want to recognize a cylindrical cut as an **extrusion, a revolve, or a hole**» | там само |
| окремий розділ «Diagnostic Error Messages» — відмова розпізнати є **штатним режимом** | c_Diagnostic_Error_Messages.htm |

І головне: **вхід FeatureWorks — імпортоване СОЛІДНЕ ТІЛО (STEP/IGES/Parasolid), не меш.** Метрик Dassault
не публікує → **не виміряно**.

Siemens NX Synchronous Technology — docs.sw.siemens.com закрито логіном, публічні сторінки технічних
гарантій не містять → **не перевірено, не цитувати**.

---

## 6. Головна відповідь: де рветься ланцюг і скільки лишається людині

### 6.1 Карта розривів

| Ланка | Стан | Доказ |
|---|---|---|
| зображення → меш | працює | (розібрано в `README.md` §4: TripoSR/TRELLIS/Hunyuan3D) |
| меш → **масштаб у мм** | **розрив** | масштаб з одного фото невідновний; усі мережі нормалізують вхід |
| меш → B-rep фасетами | «працює», але марно | **[виміряно]** 124 трикутники → 124 `Plane`, `Cylinder` не з'являється |
| меш → сегментація + фітинг примітивів | працює частково, **мм зберігаються** | CGAL absolute `epsilon`; **[виміряно]** переріз+МНК дає Ø19.974 при 20 |
| сегментація → **повний B-rep з топологією** | працює лише у ComplexGen | Gurobi+Mosek, CUDA, Windows-only фаза, **545 с/модель** |
| B-rep → мітки на грані | добре на синтетиці, посередньо на реальних | ABC per-class **56.81 %**, IoU **50.37 %** |
| мітка → **фіча з ескізом** | **розрив, ланка відсутня** | «eight labels for each face» — і все |
| меш → sketch+extrude напряму | формально є, непридатно | вісь **8.137°**, CD **13.889**, CAD-експорт = stub |
| ескіз → **constraint-и і розміри** | **розрив** | у ML немає ніде; тільки FeatureWorks (платний SW) |
| **порядок операцій (дерево)** | **розрив** | «does not make use of a known ordering» |
| округлення до інженерних значень | ніде не робиться | — |

### 6.2 Скільки людської роботи лишається

| Крок | Що закриває автоматика | Що лишається людині |
|---|---|---|
| 1. Масштаб і орієнтація | нічого | **100 %**: задати мм (еталон/розмір із креслення), вирівняти по осях, обрати базову площину |
| 2. Чистка меша | напівавто (візарди Design X / Fusion Repair) | вибір параметрів, око людини |
| 3. Сегментація на поверхні | CGAL/Open3D/`SegmentationBestFit`; UV-Net ~50 % IoU на реальних | **≈половину сегментів перевірити/перепризначити** |
| 4. Фітинг примітива → число в мм | **працює** (`Reen_ApproxCylinder`, CGAL, власний МНК) | підтвердження |
| 5. Число → **фіча** («R 5.03 → Hole Ø10») | **нічого** | **100 %**: рішення про семантику + округлення до стандартного ряду |
| 6. Ескіз: побудова | **нічого** | **100 %**: площина, проєкція контуру, обрізка |
| 7. Ескіз: constraint-и і розміри | **нічого** (крім платного FeatureWorks) | **100 %**: horizontal/vertical/tangent/concentric до fully-constrained |
| 8. Порядок операцій | **нічого** | **100 %**: що база, що виріз, у якому порядку |
| 9. Fillet/chamfer/revolve/pattern | **нічого** (SECAD-Net: future work) | **100 %** |
| 10. Перетинні фічі | ламають і AAG, і FeatureWorks | **100 %**: розчепити перетин вручну |
| 11. Верифікація допусків | нічого | звірка з кресленням |

**Коротка відповідь на питання користувача:** автоматика здатна дати (а) чисте тіло замість меша,
(б) **числа в мм для окремих поверхонь** (радіус циліндра, площина, відстань), (в) гіпотезу «ці грані —
одна фіча» з надійністю ~50 % IoU на реальних деталях. **Весь параметричний зміст — ескіз, обмеження,
розмір, порядок операцій — лишається людині або нашому rule-based коду.** Це не «автоматичне відновлення
дерева», це «розумне виділення поверхонь + підказка».

Заявлених вендорами цифр «X годин від скана до параметричного соліда» не існує **у жодного** з
Hexagon / Autodesk / Dassault → **не виміряно**. Єдині тверді числа, які дало це дослідження:
Fusion — ліміт **10 000** mesh-елементів; Design X — **11** етапів воркфлоу і від **$1 900**/рік;
ComplexGen — **545 с** ILP на модель; CAPRI-Net — **3 хв** GPU на деталь; ASM — **64.57 %** accuracy;
UV-Net на ABC — **50.37 %** IoU; Point2Cyl — **8.137°** похибки осі.

---

## 7. Що з цього НЕ треба робити

1. **Не будувати міст на `makeShapeFromMesh` + `refine` + `Part.Solid`.** Код і **[виміряно]** доводять:
   1 трикутник = 1 площина; `refine` зливає лише співпадаючі площини з `Precision::Confusion` ≈ 1e-7;
   циліндра не буде ніколи, а 100 k+ граней зроблять документ непридатним. Це не «шлях, який треба
   доопрацювати» — це глухий кут.
2. **Не шукати в OCCT функції «меш → аналітична поверхня».** `ConvertToAnalytical` і
   `ShapeAnalysis_CanonicalRecognition` приймають B-rep-поверхню. `BRepMesh` — тільки в інший бік.
3. **Не будувати продакшн на `ReverseEngineering.sampleConsensus`.** **[виміряно]**: `Cylinder` і `Cone`
   дають **Segmentation fault**, `Sphere` мовчки повертає сміття (R=√200 на циліндрі), і половина API
   існує лише під `#if HAVE_PCL_*`.
4. **Не тягнути Point2CAD.** Конфлікт LICENSE (Apache-2.0) vs README (CC-BY-NC); PyMesh не збирається на ARM;
   він **вимагає** готову сегментацію, тобто тягне ParSeNet/HPNet зі стеком 2020 (`tensorflow-gpu 1.15`).
5. **Не планувати ComplexGen.** Gurobi+Mosek з ліцензійним сервером, MinkowskiEngine (CUDA, Ubuntu),
   фаза 3 «compiled only under Windows», 545 с на модель. Три незалежні дискваліфікатори.
6. **Не брати ParSeNet / HPNet / SED-Net / NEF.** У жодного немає файлу LICENSE → юридично «всі права
   застережено». Ваги на Google Drive / OneDrive / Baiduyun → офлайн-дистрибуція невідтворювана.
7. **Не брати BRepNet ні в якому вигляді** — CC BY-NC-SA 4.0 (NonCommercial + ShareAlike), включно з вагами.
   Не брати CSGNet / D²CSG / InverseCSG — **файлу LICENSE немає**.
8. **Не обіцяти «feature recognition» на базі UV-Net/BRepNet/H-CADNet.** Вони дають мітку на грань.
   І на реальному ABC це 56.81 % per-class / 50.37 % IoU.
9. **Не цитувати «99.95 %» без слова MFCAD.** Це синтетичний куб із наліпленими фічами; на реальних деталях
   цифра на 43 пункти нижча.
10. **Не сподіватись на SECAD-Net як експортер CAD** — це stub, віддає меш. І не на Point2Cyl:
    вісь 8.137°, CD 13.889.
11. **Не називати «параметричним» те, що Fusion називає Parametric** — це один вузол `Convert Mesh`
    у таймлайні, при ліміті ~10 000 елементів.
12. **Не обіцяти «як у Geomagic Design X»** — Windows-only, від $1 900/рік, і дерево фіч там будує **людина**.
13. **Не вважати CGAL безкоштовним обідом** — Shape_detection це GPL-3.0+ або комерційна; вона перетворить
    увесь воркбенч на GPL-3.0.
14. **Не планувати `pythonocc-core` через pip** — 0 файлів на PyPI. І не `dgl` на macOS — тільки win_amd64.
15. **Не обіцяти «відновимо оригінальне дерево фіч».** Задача недовизначена: «a designer may choose to create
    a cylinder with an extruded circle or a revolved rectangle».

---

## 8. Що з цього реально доступне як pip-бібліотека офлайн

Перевірено через `https://pypi.org/pypi/<pkg>/json` (2026-08-12):

| Пакет | Версія | Ліцензія | macOS ARM wheel | Вирок |
|---|---|---|---|---|
| `cadquery-ocp` | 7.9.3.1.1 | Apache-2.0 (обгортка); OCCT — LGPL-2.1 + OCCT Exception | **так** (`macosx_11_0_arm64`, +x86_64, win_amd64, manylinux; 25 файлів) | **єдиний робочий pip-шлях до OCCT**, офлайн, без CUDA |
| `cadquery` / `build123d` | 2.8.0 / 0.11.1 | Apache-2.0 | py3-none-any | API поверх того самого OCP |
| `open3d` | 0.19.0 | MIT | так (`universal2`, cp310–312) | KD-tree, нормалі, I/O, RANSAC **лише площини** |
| `trimesh` | 5.0.0 | MIT | py3-none-any | меш I/O, перерізи |
| `pyransac3d` | 0.6.0 | Apache-2.0 | py3-none-any | простий, **циліндр слабкий за визнанням авторів** |
| `cgal` | 6.0.1 | **GPL-3.0+ OR комерційна** | так | Efficient RANSAC (5 примітивів), але GPL-зараження + параметри рядком |
| `pymeshlab` | 2025.7 | **GPL3** | так | чистка/ремешинг, але GPL |
| `numpy-stl` | 4.0.0 | BSD-3 | py3-none-any | STL I/O |
| `pythonocc-core` | 0.16 | LGPL | **0 файлів у релізі** | pip не працює |
| `freecad` | — | — | **404** | FreeCAD у pip не постачається |
| `dgl` | 2.2.1 | — | **тільки win_amd64** | блокує UV-Net на macOS |
| `pymesh2` | 0.1.14 | MPL | лише cp35 x86_64 | блокує Point2CAD / CAPRI-Net / D²CSG |
| `uvnet`, `brepnet`, `occwl`, `secad`, `point2cad`, `complexgen` | — | — | — | **404, не існують** |

**Найдешевший LGPL-сумісний стек, який реально працює офлайн і зберігає мм:**
FreeCAD (LGPL-2.1+, вже є: `Mesh`, `MeshPart`, `Part`, `ReverseEngineering.approxSurface/approxCurve`,
`Mesh SegmentationBestFit`) + `numpy`/`scipy` для власного МНК-фітингу площини/циліндра/кола.
Ані CGAL (GPL), ані PyMeshLab (GPL), ані жодна мережа для цього не потрібні: **[виміряно]** переріз меша +
МНК-коло дає Ø19.974 при істинних 20 (0.13 %), а `MeshCore::CylinderFit` уже вбудований у FreeCAD і віддає
`Radius`/`Height`/`Placement` як редаговані властивості.

---

## 9. Відтворення власних вимірів

FreeCAD 1.1.3 (`/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd`), macOS ARM, 2026-08-12.

```python
import FreeCAD, Part, MeshPart
c = Part.makeCylinder(10, 20)                       # 3 грані: Cylinder + 2 Plane
for dev in (1.0, 0.5, 0.1, 0.05, 0.01):
    m = MeshPart.meshFromShape(Shape=c, LinearDeflection=dev, AngularDeflection=0.5, Relative=False)
    s = Part.Shape(); s.makeShapeFromMesh(m.Topology, 0.1)
    sol = Part.Solid(s); r = s.removeSplitter()     # removeSplitter == Part RefineShape
    print(dev, m.CountFacets, len(s.Faces),
          100*(sol.Volume-c.Volume)/c.Volume, len(r.Faces),
          set(f.Surface.__class__.__name__ for f in r.Faces))
```
→ `Cylinder` не з'являється в жодному рядку (таблиця у §3.1).

```python
secs = m.crossSections([((0,0,10),(0,0,1))], 1e-6)  # 91 точка
# МНК-коло по точках перерізу → D = 19.9741 мм (істинне 20), max residual 0.1226 мм
```

```python
import Points, ReverseEngineering as RE
pc = Points.Points(); pc.addPoints([FreeCAD.Vector(p.x,p.y,p.z) for p in m.Topology[0]])
RE.sampleConsensus(SacModel="Plane",    Points=pc)  # ок
RE.sampleConsensus(SacModel="Sphere",   Points=pc)  # «успіх», але R=14.142 — сміття
RE.sampleConsensus(SacModel="Cylinder", Points=pc)  # Segmentation fault 11 (exit 139)
RE.sampleConsensus(SacModel="Cone",     Points=pc)  # Segmentation fault 11 (exit 139)
```

---

## 10. Висновок для FCC

Міст існує рівно на одну ланку: **сегмент меша → фіт примітива → число в мм**. Вона вже є у FreeCAD
(`Reen_ApproxCylinder`/`ApproxPlane`/`ApproxSphere`, `Mesh SegmentationBestFit`), LGPL, офлайн, без CUDA,
і **[виміряно]** дає 0.13 % похибки на чистому мешу. Ланки «число → фіча», «фіча → ескіз із constraint-ами»
і «фічі → порядок операцій» **не автоматизовані ніде** — ні в науці, ні в комерції, ні у FreeCAD.

Це той самий вирок, що й у `README.md` §5 про фото: **як референс — так, як джерело розмірів і структури — ні.**
Меш може бути підказкою («тут циліндр R≈5.03, схоже на отвір Ø10»), але дерево фіч і числа мусять
прийти з креслення або від користувача — тобто з тієї гілки, яку FCC вже будує.

Якщо колись знадобиться демонстрація «меш → модель», єдина чесна форма — **асистент ручного ретопо**:
імпорт меша як референсу → фітинг на виділенні → пропозиція «Hole Ø10 на площині XY?» → людина підтверджує →
воркбенч створює **справжній** `Sketch` + `Pad`/`Pocket` у мм із constraint-ами. Автоматичного end-to-end
не обіцяти: первинних джерел, які його підтверджують, не існує.
