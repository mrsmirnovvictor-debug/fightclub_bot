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

# Первый бакет, из которого пока не переехали кеды
LEGACY = "https://pub-ea6a4494c019470aa38328eec255511d.r2.dev/VEGAS%20Fight%20Club"


def avatar(code: str) -> str:
    """Картинка образа лежит под его же кодом: avatars/rookie.jpeg."""
    return f"{AVATARS}/{code}.jpeg"


def slot(code: str) -> str:
    """Подложка пустого слота — тоже под своим кодом: slots/weapon.jpeg."""
    return f"{SLOTS}/{code}.jpeg"


__all__ = [
    "ADDED",
    "AVATARS",
    "BUCKET",
    "ITEMS",
    "MAGIC",
    "LEGACY",
    "SLOTS",
    "WEAPONS",
    "avatar",
    "slot",
]
