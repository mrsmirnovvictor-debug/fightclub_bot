# Промт для генерации картинок приёмов

Шестнадцать картинок, по одной на приём. Формат **16:9, горизонтальный**
(1920×1080 или 1280×720) — в отличие от вещей и модификаторов, которые
квадратные: приём показывается широкой плашкой под кнопками удара, а не
плиткой на прилавке.

Файлы кладутся в тот же бакет и по тому же правилу, что вещи:
`items/<code>.jpeg`. Коды — в таблице ниже, они же имена файлов. Поле
`image=` в коде задавать не нужно, адрес собирается сам.

---

## Общий промт

> Horizontal 16:9 key art for a fighting-club game ability card. Gritty
> urban underground fight club setting, late-night basement lighting.
> Single dramatic action captured mid-motion, shot like a frozen frame of
> a fight: strong silhouette reading clearly at small size, shallow depth
> of field, dust and sweat in the air.
>
> Palette: desaturated concrete greys and cold blue shadows, with ONE
> saturated accent colour carrying the ability (specified per image).
> Heavy rim light on the fighter, dark vignetted corners.
>
> Composition: subject occupies the left two thirds, right third is
> darker and less busy — the interface prints the ability name and its
> energy cost over it, so keep that area quiet.
>
> No text, no letters, no numbers, no logos, no watermarks, no UI frames,
> no borders. No gore, no blood pools, no wounds — impact is shown by
> motion, light and debris, not by injury. Faces obscured or turned away:
> these are nobody in particular, they are the move itself.
>
> Style: semi-realistic digital painting, cinematic, high contrast,
> painterly texture, film grain. Not cartoon, not anime, not 3D render,
> not photobash.

Дальше к общему промту дописывается строка конкретного приёма.

---

## Приёмы

Акцентный цвет у каждой ветки свой — по нему приём узнают на бегу, ещё до
того, как прочитают название. Внутри ветки цвет один, а различает ступени
насыщенность и размах движения: «Сильный удар» — короткий выпад, «Массовый
удар» — удар, от которого расходится волна.

### Ветка удара — акцент янтарно-оранжевый (#e8a33d)

| Код файла | Приём | Строка промта |
|---|---|---|
| `strong_hit` | Сильный удар | A bare-knuckle straight punch landing, amber impact flash at the point of contact, air rippling outward in a tight cone. Compact, controlled, close range. |
| `power_hit` | Мощный удар | A heavy hooking punch at full extension, amber shockwave tearing outward, the fighter's whole body torqued into it, dust lifting off the floor. |
| `crushing_hit` | Сокрушительный удар | A downward hammer blow, molten amber cracks splitting the concrete beneath the impact, debris thrown upward, floodlight flaring behind. |
| `mass_hit` | Массовый удар | A ground-shaking blow at the centre of a ring, an amber ring of force expanding outward through several staggered silhouettes at the edges of frame, all thrown off balance at once. |

### Ветка уворота — акцент бирюзовый (#3ec8c1)

| Код файла | Приём | Строка промта |
|---|---|---|
| `nimble` | Проворность | A fighter bending impossibly out of the path of an incoming fist, teal motion trails marking where the body just was, the punch passing through empty air. |
| `cunning` | Хитрость | A fighter slipping a punch and already turning into the answer, teal trail curving from the evasion straight into a rising counter-fist. |
| `guile` | Коварство | A fighter ghosting aside and driving a counter-blow home, teal evasion trail meeting a white-hot critical flash at the point of the answer. |
| `trickster_god` | Бог обмана | One fighter mid-evasion surrounded by teal after-images of himself, wisps of the same teal light drifting toward allied silhouettes behind him. Mythic, unsettling, several bodies where there should be one. |

### Ветка крита — акцент алый (#e8483d)

| Код файла | Приём | Строка промта |
|---|---|---|
| `crit_hit` | Критический удар | A precise strike finding an opening, a thin crimson flash at the exact point of contact, everything else in shadow. Surgical, not wild. |
| `breach` | Пролом | A strike smashing straight through a raised guard, forearms flung apart, crimson light bursting through the broken defence. |
| `deadly_hit` | Смертельный удар | A finishing blow at the moment of landing, crimson light blooming outward, the struck silhouette already folding. Final, heavy, unanswerable. |
| `blood_call` | Призыв к крови | A fighter mid-strike with crimson light spreading from him toward allied silhouettes at the frame edges, as if the whole room caught the same fury. |

### Ветка стойкости — акцент зелёный для лечения (#3ea84a), стальной для защиты (#8fa3b8)

| Код файла | Приём | Строка промта |
|---|---|---|
| `recovery` | Восстановление | A battered fighter drawing breath and straightening up, warm green light gathering along the ribs and shoulders where the damage was. Quiet, stubborn, no spectacle. |
| `will_to_win` | Воля к победе | A fighter rising off one knee, green light running up through the whole body, head lifting into the floodlight. Defiance rather than healing. |
| `life_master` | Мастер жизни | A standing fighter with green light pouring off him and reaching allied silhouettes around him, all of them straightening at once. Calm centre, exhausted room. |
| `parry` | Парирование | A full-force punch stopped dead against a raised forearm, cold steel-grey light flaring at the contact, absolutely no give — the attacker's arm buckling, the defender unmoved. |

---

## Что проверить перед заливкой

1. **Читается ли силуэт**, если уменьшить картинку до ширины пальца: в бою
   плашка маленькая, и приём узнают по позе и цвету, а не по деталям.
2. **Правая треть тихая** — там печатается название и цена.
3. **Цвет ветки не перепутан.** Четыре ветки, четыре цвета; если «Пролом»
   выйдет бирюзовым, игрок прочитает его как уворот.
4. **Ступень видна по размаху.** Внутри ветки картинки должны выстраиваться
   в лестницу: чем выше ступень, тем шире движение и больше света.
5. **Никакого текста на картинке** — ни цифр, ни названий. Всё подписывает
   интерфейс.
