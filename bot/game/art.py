"""Где лежат картинки клуба.

Один адрес на весь проект: и оружие, и щиты, и образы бойцов живут в общем
бакете R2, просто в разных папках. Меняется хранилище — правится одна строка.
"""

from __future__ import annotations

BUCKET = "https://pub-44581ebfe3a240b9b46b8d169429b1c0.r2.dev"

WEAPONS = f"{BUCKET}/weapons"
# Второй заход по недостающим позициям лёг в отдельную папку
ADDED = f"{BUCKET}/add"
AVATARS = f"{BUCKET}/avatars"
SLOTS = f"{BUCKET}/slots"
ITEMS = f"{BUCKET}/items"
# Футболки — самый молодой раздел, его рисовали отдельным заходом
SHIRTS = f"{BUCKET}/shirts"
# Рейд-боссы
BOSSES = f"{BUCKET}/bosses"
# Товар лавки мага
MAGIC = f"{BUCKET}/magic"
POTIONS = f"{BUCKET}/potions"
# Карты районов города: по карте на район
LOCATIONS = f"{BUCKET}/locations"
# Виды изнутри: по картинке на дом. Папки две, и это не наша прихоть —
# первые четырнадцать домов выгрузили в locations/interiors, следующие
# шестнадцать легли рядом с корнем. Переименовывать чужой бакет не наше
# дело, поэтому у дома написано, в какой он папке
INTERIORS = f"{LOCATIONS}/interiors"
# Районы второй очереди: VCPD, больница, казармы и всё остальное
NEW_INTERIORS = f"{BUCKET}/interiors"


def avatar(code: str) -> str:
    """Картинка образа лежит под его же кодом: avatars/rookie.jpeg."""
    return f"{AVATARS}/{code}.jpeg"


def slot(name: str) -> str:
    """Подложка пустого слота по имени файла: slots/weapon.png.

    Обычно имя совпадает с кодом слота, но не всегда: клетка второй руки
    рисуется щитом, а клетка «тело» — силуэтом футболки. Поэтому сюда
    приходит имя файла целиком, с расширением.

    Подложки лежат в png, а не в jpeg: это плоские силуэты в 256 пикселей,
    и такую картинку png жмёт лучше фотоформата. Фон у них свой, тёмный —
    прозрачность оказалась не по зубам генератору, — поэтому на светлой теме
    вёрстка их приглушает. Не загрузилась — слот покажет значок, как и раньше.
    """
    return f"{SLOTS}/{name}"


def shirt(code: str) -> str:
    """Футболка лежит под кодом вещи: shirts/club_tee.jpeg."""
    return f"{SHIRTS}/{code}.jpeg"


def item(code: str) -> str:
    """Вещь лежит под своим кодом: items/riot_shield.jpeg.

    Так адрес картинки не приходится писать руками: новая вещь получает
    его от собственного кода. Старые файлы с именами вроде
    `bandana.jpeg_202608281514.jpeg` под правило не подходят — у таких
    вещей адрес до сих пор задан явно, и `Item.image` его перебивает.
    """
    return f"{ITEMS}/{code}.jpeg"


def location(code: str, ext: str = "jpeg") -> str:
    """Карта района: locations/main_hub.jpeg.

    Формат приходится называть: первую очередь города отдали в jpeg,
    вторую — в png. Перегонять её в другой формат незачем, поэтому
    формат написан у района, а имя файла всё так же считается от кода.
    """
    return f"{LOCATIONS}/{code}.{ext}"


def interior(code: str, folder: str = INTERIORS) -> str:
    """Вид изнутри дома: locations/interiors/pharmacy_interior.jpeg.

    Снаружи дом — кусочек нарисованного района, и с карты видно только
    дверь. Внутри же боец проводит всё время, и без картинки лавка от
    аптеки отличается одним заголовком.

    Папку приходится называть: интерьеры выгружали двумя заходами и в
    разные места. Имя файла при этом всё так же считается от кода дома.
    """
    return f"{folder}/{code}.jpeg"


def boss(code: str) -> str:
    """Портрет рейд-босса: bosses/cellar_boss.png."""
    return f"{BOSSES}/{code}.png"


def potion(code: str) -> str:
    """Склянка лежит под кодом эликсира: potions/heal_small.jpeg."""
    return f"{POTIONS}/{code}.jpeg"


__all__ = [
    "ADDED",
    "AVATARS",
    "BOSSES",
    "BUCKET",
    "INTERIORS",
    "ITEMS",
    "LOCATIONS",
    "MAGIC",
    "NEW_INTERIORS",
    "POTIONS",
    "SHIRTS",
    "SLOTS",
    "WEAPONS",
    "avatar",
    "boss",
    "interior",
    "item",
    "location",
    "potion",
    "shirt",
    "slot",
]
