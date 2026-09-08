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
    "ITEMS",
    "MAGIC",
    "POTIONS",
    "SHIRTS",
    "SLOTS",
    "WEAPONS",
    "avatar",
    "boss",
    "potion",
    "shirt",
    "slot",
]
