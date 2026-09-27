# Промт для карточки страхового полиса

Одна картинка: `magic/insurance_policy.jpeg`. Адрес задан в
`bot/game/insurance.py` полем `IMAGE`, менять вёрстку под неё не нужно.

## Формат

**Квадрат 1:1** (1024×1024 или больше), ровная заливка фона **`#60656b`** до
краёв — тот же фон, на котором собран весь инвентарь клуба. Полис показывается
плиткой рядом с вещами и товаром мага, и на другом фоне он выглядел бы
наклейкой среди своих.

Без рамок, без виньеток, без скруглённых углов: рамку рисует сама вёрстка,
а нарисованная поверх неё читается как вторая.

## Что на картинке

Не человек и не больница — **сам документ**. Полис в клубе первый документ
вообще, и плитка должна читаться как «бумага», а не как «услуга»: в
«Документах» под ней будет бланк с именем и сроком, и картинка обещает
именно бланк.

## Общий промт

> Square 1:1 product shot of a single folded insurance document lying flat,
> centred, occupying about 70% of the frame with even margins. Flat
> `#60656b` studio background filling the frame edge to edge, soft top-left
> key light, one soft shadow under the paper.
>
> The document is a heavy cream-coloured card stock certificate, slightly
> worn at the corners, with a faint guilloche security pattern and a
> letterpress-embossed seal in the lower right — a round seal with a
> stylised laurel and a single die pip at its centre. A dark red wax stamp
> overlaps the seal's edge. A brass paperclip holds a smaller carbon-copy
> slip to the top left corner. One clean horizontal fold crease across the
> middle.
>
> Palette: warm cream paper, desaturated ink navy for the printed rules,
> muted brass for the seal, one deep crimson accent in the wax. Cold grey
> background, no colour cast on the paper.
>
> No text, no letters, no numbers, no logos, no watermarks, no signatures,
> no barcodes, no QR codes — every line of print is an unreadable
> engraved texture, never actual characters. No hands, no people, no
> medical props, no crosses, no hospital imagery, no stethoscope.
>
> Style: clean 3D product render, physically based materials, sharp focus
> across the whole document, subtle paper grain. Not flat illustration,
> not cartoon, not anime, not photobash, not a mockup with editable text.

## Почему «никакого текста» здесь важнее обычного

На всех остальных картинках запрет на буквы — про аккуратность: подпись
делает интерфейс. Здесь он про смысл. Документ с напечатанным названием
и номером спорил бы с бланком под ним: у каждого бойца свой номер, своё
имя и свой срок, и они печатаются вёрсткой из его полиса. Нарисованный
номер был бы чужим — и это первое, что игрок заметит.

Генератор охотно рисует «CERTIFICATE OF INSURANCE» на таком кадре,
поэтому запрет стоит повторить в промте дважды: один раз в списке
запретов, второй — как требование к самой печати («unreadable engraved
texture»).

## Что проверить перед заливкой

1. **Фон ровный `#60656b`** до самых краёв, без градиента и без виньетки:
   плитка стоит в сетке рядом с вещами.
2. **Ни одной читаемой буквы и цифры.** Печать — текстура, а не текст.
   Имя, номер и срок печатает интерфейс.
3. **Ни медицинской символики, ни креста.** Полис страхует жизнь и
   здоровье, но это документ конторы, а не аптечка: крест на картинке
   отправит игрока искать её в больнице.
4. **Читается ли плитка размером в палец.** Должно быть видно: бумага,
   печать, скрепка. Остальное — текстура.
5. **Углы свободны.** Вёрстка печатает поверх плитки состояние документа,
   и два верхних угла должны быть тихими.
