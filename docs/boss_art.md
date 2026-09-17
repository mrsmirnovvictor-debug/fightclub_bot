# Промты на снаряжение Босса Казино

Девять позиций — весь его комплект. Босс танк: в правой руке кувалда, в левой
штурмовой щит.

Стиль тот же, что у остального инвентаря: квадрат 1:1, ровная заливка фона
**`#60656b`** до краёв, студийный свет, 3D-рендер, вещь занимает около 70%
высоты кадра с равными полями. Без рамок и виньеток — они читаются моделью как
панель и ломают сетку инвентаря.

> **Разночтение, которое стоит знать.** В старом файле промтов на одежду фон
> указан как `#6B6D70`, а в README и `docs/content.md` — `#60656b`. Здесь везде
> `#60656b`: на нём собран инвентарь, и по нему же красится клетка календаря.
> Если старые картинки рисовались на `#6B6D70`, разница на глаз почти не видна,
> но новую вещь лучше ставить на тот фон, который считает кодом весь остальной
> клуб.

## Чем босс отличается от прилавка

Те же вещи носит и игрок, поэтому босса нужно отличать не формой, а
**состоянием и меткой**. Три приёма, проходящие через все девять промтов:

* **Латунь и бордо вместо стали и серого.** У прилавочных вещей металл
  стальной; у босса — потемневшая латунь и бордовая кожа. Этого хватает, чтобы
  комплект читался как чужой, ещё до того как разглядишь детали.
* **Метка казино.** Игральная фишка, карточная масть или клеймо «VEGAS» —
  по одной метке на вещь, не больше. Босс держит казино, а не клуб.
* **Он победитель, а не битый.** У прилавочных вещей потёртости от носки; у
  босса — следы чужих ударов: вмятины, зарубки, царапины **поверх** латуни.
  Сама вещь при этом целая и ухоженная.

**Ракурсы** закреплены за типом вещи — те же, что на прилавке, иначе на кукле
бойца комплект разъедется:

| Тип | Как подаём |
|---|---|
| Шлем | вид спереди, чуть сверху, пустой — без головы внутри |
| Оружие | по диагонали кадра, боёк вниз-влево, рукоять вверх-вправо |
| Щит | лицом в камеру, чуть в три четверти, ремни не видны |
| Футболка, куртка | разложены плашмя, лицом вверх, рукава вниз, плечи ровно |
| Пояс | свёрнут в кольцо, пряжка внизу и лицом в камеру |
| Перчатки | пара рядом, одна слегка внахлёст на другую |
| Штаны | сложены плашмя, вид спереди, штанины прямо |
| Обувь | пара рядом, вид спереди в три четверти |

**Негативный промт** — во все девять, если инструмент его поддерживает:

> frame, border, panel, vignette, rounded corners, text overlay, watermark, real brand logo, real casino name, mannequin, human body, hands, model, multiple copies, collage, glowing magic effects, neon

**Имена файлов** — по кодам вещей. Кувалда лежит в `items/`, остальные восемь
заменяют собой картинки прилавочных вещей, поэтому **им нужны свои коды**: без
отдельных кодов новая картинка перекрасит вещь и в магазине. Пока заведён
только код кувалды — `boss_sledge.jpeg`. Остальные восемь файлов назовите
`boss_helmet.jpeg`, `boss_shield.jpeg`, `boss_tee.jpeg`, `boss_belt.jpeg`,
`boss_gloves.jpeg`, `boss_jacket.jpeg`, `boss_pants.jpeg`, `boss_boots.jpeg` —
под эти имена я заведу вещи, когда картинки будут готовы.

**Общий хвост** — одинаковый у всех девяти:

> …Fictional “VEGAS” casino branding, original playing-card and chip motifs, tarnished brass and deep burgundy, dents and notches from other people’s blows over well-kept gear. Underground casino boss, not a street fighter. Clearly fictional design, no real-world logos, no real casino names. Single object only, no body inside it, centered, occupying about 70% of the frame height with equal margins. Background fills the square edge to edge — no frame, no border, no panel, no vignette. Solid cool medium-gray background #60656b, soft studio lighting, subtle contact shadow, semi-realistic stylized 3D game render, 1:1 square.

---

## Оружие

### `boss_sledge` — Кувалда Босса (6–34 урона, точность 0.12)

Числа у неё нарочно не прилавочные: редко доходит и почти сносит, когда
доходит. Это должно быть видно — тяжёлая голова, длинная рукоять, замах,
который не остановить на полпути.

> Square 1:1 game inventory icon for “VEGAS Fight Club”. Massive two-handed sledgehammer laid diagonally across the frame, head down-left, grip up-right, oversized blackened steel head with a brass-inlaid face, deep dents and chipped edges on the striking face, long hardwood shaft wrapped in worn burgundy leather cord, brass collar and a heavy brass pommel cap, dried grime in the seams. Fictional “VEGAS” casino branding stamped into the brass collar, original playing-card and chip motifs, tarnished brass and deep burgundy, dents and notches from other people’s blows over well-kept gear. Underground casino boss, not a street fighter. Clearly fictional design, no real-world logos, no real casino names. Single object only, no body inside it, centered, occupying about 70% of the frame height with equal margins. Background fills the square edge to edge — no frame, no border, no panel, no vignette. Solid cool medium-gray background #60656b, soft studio lighting, subtle contact shadow, semi-realistic stylized 3D game render, 1:1 square.

## Щит

### `boss_shield` — Щит Босса (вместо штурмового щита)

> Square 1:1 game inventory icon for “VEGAS Fight Club”. Heavy riot shield seen face on, very slightly three-quarter, straps hidden behind, dark polycarbonate face over a brass-edged frame, a broad burgundy diagonal band across it, scuffed viewport, rows of hammer dents and knife scratches across the surface, brass rivets along the rim. Fictional “VEGAS” casino branding across the band, original playing-card and chip motifs, tarnished brass and deep burgundy, dents and notches from other people’s blows over well-kept gear. Underground casino boss, not a street fighter. Clearly fictional design, no real-world logos, no real casino names. Single object only, no body inside it, centered, occupying about 70% of the frame height with equal margins. Background fills the square edge to edge — no frame, no border, no panel, no vignette. Solid cool medium-gray background #60656b, soft studio lighting, subtle contact shadow, semi-realistic stylized 3D game render, 1:1 square.

## Голова

### `boss_helmet` — Шлем Босса (вместо мотошлема)

> Square 1:1 game inventory icon for “VEGAS Fight Club”. Open-face motorcycle helmet seen from the front and slightly above, empty — no head inside it, deep burgundy lacquered shell with a brass trim line, smoked visor flipped up, brass-buckled chin strap, quilted dark lining, a cluster of shallow dents across the crown. Fictional “VEGAS” casino branding on the brow, original playing-card and chip motifs, tarnished brass and deep burgundy, dents and notches from other people’s blows over well-kept gear. Underground casino boss, not a street fighter. Clearly fictional design, no real-world logos, no real casino names. Single object only, no body inside it, centered, occupying about 70% of the frame height with equal margins. Background fills the square edge to edge — no frame, no border, no panel, no vignette. Solid cool medium-gray background #60656b, soft studio lighting, subtle contact shadow, semi-realistic stylized 3D game render, 1:1 square.

## Куртка

### `boss_jacket` — Бронекуртка Босса (вместо бронекуртки вышибалы)

> Square 1:1 game inventory icon for “VEGAS Fight Club”. Heavy armoured bouncer jacket laid flat and face up, sleeves straight down, shoulders level, deep burgundy leather over segmented dark armour panels at the chest and shoulders, brass zip and brass shoulder studs, quilted collar, scored and gouged panels with the leather intact around them. Fictional “VEGAS” casino back print showing at the collar, original playing-card and chip motifs, tarnished brass and deep burgundy, dents and notches from other people’s blows over well-kept gear. Underground casino boss, not a street fighter. Clearly fictional design, no real-world logos, no real casino names. Single object only, no body inside it, centered, occupying about 70% of the frame height with equal margins. Background fills the square edge to edge — no frame, no border, no panel, no vignette. Solid cool medium-gray background #60656b, soft studio lighting, subtle contact shadow, semi-realistic stylized 3D game render, 1:1 square.

## Футболка

### `boss_tee` — Кевларовая футболка Босса

> Square 1:1 game inventory icon for “VEGAS Fight Club”. Sleeveless kevlar-weave undershirt laid flat and face up, shoulders level, dark charcoal aramid fabric with a deep burgundy woven trim at the neck and armholes, visible weave texture, a few frayed threads and faint dark stains, small brass eyelets at the hem. Fictional “VEGAS” casino woven tag at the neck, original playing-card and chip motifs, tarnished brass and deep burgundy, dents and notches from other people’s blows over well-kept gear. Underground casino boss, not a street fighter. Clearly fictional design, no real-world logos, no real casino names. Single object only, no body inside it, centered, occupying about 70% of the frame height with equal margins. Background fills the square edge to edge — no frame, no border, no panel, no vignette. Solid cool medium-gray background #60656b, soft studio lighting, subtle contact shadow, semi-realistic stylized 3D game render, 1:1 square.

## Пояс

### `boss_belt` — Силовой пояс Босса

> Square 1:1 game inventory icon for “VEGAS Fight Club”. Wide weightlifting-style power belt coiled into a single ring, oversized cast brass buckle at the bottom facing the camera, thick burgundy leather, tooled card-suit pattern along the strap, extra punched holes, darkened creases, brass edge studs. Fictional “VEGAS” casino cast into the buckle, original playing-card and chip motifs, tarnished brass and deep burgundy, dents and notches from other people’s blows over well-kept gear. Underground casino boss, not a street fighter. Clearly fictional design, no real-world logos, no real casino names. Single object only, no body inside it, centered, occupying about 70% of the frame height with equal margins. Background fills the square edge to edge — no frame, no border, no panel, no vignette. Solid cool medium-gray background #60656b, soft studio lighting, subtle contact shadow, semi-realistic stylized 3D game render, 1:1 square.

## Перчатки

### `boss_gloves` — Перчатки Босса (вместо битых перчаток)

> Square 1:1 game inventory icon for “VEGAS Fight Club”. Pair of heavy fighting gloves side by side, one slightly overlapping the other, empty — no hands inside them, burgundy leather with brass knuckle plates across the backs, reinforced padded palms, brass wrist buckles, scraped plates and scuffed knuckles, stitching pulled at one seam. Fictional “VEGAS” casino stamping on the cuff, original playing-card and chip motifs, tarnished brass and deep burgundy, dents and notches from other people’s blows over well-kept gear. Underground casino boss, not a street fighter. Clearly fictional design, no real-world logos, no real casino names. Single object only, no body inside it, centered, occupying about 70% of the frame height with equal margins. Background fills the square edge to edge — no frame, no border, no panel, no vignette. Solid cool medium-gray background #60656b, soft studio lighting, subtle contact shadow, semi-realistic stylized 3D game render, 1:1 square.

## Штаны

### `boss_pants` — Усиленные штаны Босса

> Square 1:1 game inventory icon for “VEGAS Fight Club”. Reinforced bouncer trousers folded flat, seen from the front, legs straight, dark charcoal heavy canvas with burgundy leather panels at the thighs and knees, brass rivets at the pockets, doubled stitching, scuffed knee panels and a mended tear on one thigh. Fictional “VEGAS” casino stamping on the hip panel, original playing-card and chip motifs, tarnished brass and deep burgundy, dents and notches from other people’s blows over well-kept gear. Underground casino boss, not a street fighter. Clearly fictional design, no real-world logos, no real casino names. Single object only, no body inside it, centered, occupying about 70% of the frame height with equal margins. Background fills the square edge to edge — no frame, no border, no panel, no vignette. Solid cool medium-gray background #60656b, soft studio lighting, subtle contact shadow, semi-realistic stylized 3D game render, 1:1 square.

## Обувь

### `boss_boots` — Берцы Босса

> Square 1:1 game inventory icon for “VEGAS Fight Club”. Pair of heavy combat boots side by side, seen from the front in three-quarter view, empty — no feet inside them, oxblood leather with brass eyelets and brass toe caps, thick lugged soles, burgundy laces, scuffed toes and salt-stained welts, one heel worn down. Fictional “VEGAS” casino stamping on the heel counter, original playing-card and chip motifs, tarnished brass and deep burgundy, dents and notches from other people’s blows over well-kept gear. Underground casino boss, not a street fighter. Clearly fictional design, no real-world logos, no real casino names. Single object only, no body inside it, centered, occupying about 70% of the frame height with equal margins. Background fills the square edge to edge — no frame, no border, no panel, no vignette. Solid cool medium-gray background #60656b, soft studio lighting, subtle contact shadow, semi-realistic stylized 3D game render, 1:1 square.
