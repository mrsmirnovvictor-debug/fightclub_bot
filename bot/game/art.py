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


def slot(code: str) -> str:
    """Подложка пустого слота — под своим кодом: slots/weapon.png.

    Подложки лежат в png, а не в jpeg: это плоские силуэты в 256 пикселей,
    и такую картинку png жмёт лучше фотоформата. Фон у них свой, тёмный —
    прозрачность оказалась не по зубам генератору, — поэтому на светлой теме
    вёрстка их приглушает. Не загрузилась — слот покажет значок, как и раньше.
    """
    return f"{SLOTS}/{code}.png"


def shirt(code: str) -> str:
    """Футболка лежит под кодом вещи: shirts/club_tee.png.

    Png, а не jpeg, как у остального товара: футболки нарисованы отдельным
    заходом и залиты как есть. Формату здесь всё равно — адрес берётся из
    каталога целиком.
    """
    return f"{SHIRTS}/{code}.png"


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
