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
# Товар лавки мага
MAGIC = f"{BUCKET}/magic"
POTIONS = f"{BUCKET}/potions"


def avatar(code: str) -> str:
    """Картинка образа лежит под его же кодом: avatars/rookie.jpeg."""
    return f"{AVATARS}/{code}.jpeg"


def slot(code: str) -> str:
    """Подложка пустого слота — под своим кодом: slots/weapon.png.

    Подложки лежат в png, а не в jpeg: это плоские силуэты в 256 пикселей,
    и png их жмёт лучше фотоформата. Главное — png умеет прозрачность, и
    подложка садится на фон слота вместо того, чтобы нести свой собственный
    чёрный квадрат. Не загрузилась — слот покажет значок, как и раньше.
    """
    return f"{SLOTS}/{code}.png"


def potion(code: str) -> str:
    """Склянка лежит под кодом эликсира: potions/heal_small.jpeg."""
    return f"{POTIONS}/{code}.jpeg"


__all__ = [
    "ADDED",
    "AVATARS",
    "BUCKET",
    "ITEMS",
    "MAGIC",
    "POTIONS",
    "SLOTS",
    "WEAPONS",
    "avatar",
    "potion",
    "slot",
]
